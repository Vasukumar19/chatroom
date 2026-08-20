"""SyncManager: Partition recovery and message sync via sync_request/sync_response."""
import json
import logging
from typing import Any, Dict, List, Optional, Set
from p2p.protocol import create_envelope, validate_envelope, _now_iso

log = logging.getLogger("p2p.sync")


class SyncManager:
    """Manages periodic or event-driven metadata sync between peers."""

    def __init__(
        self,
        node_id: str,
        transport: Any,
        queue: Any,
        reliable_receiver: Any,
        route_manager: Optional[Any] = None,
    ):
        self.node_id = node_id
        self.transport = transport
        self.queue = queue
        self.reliable_receiver = reliable_receiver
        self.route_manager = route_manager

        # Register handler on transport
        try:
            self.transport.register_handler(self._on_transport_message)
        except Exception:
            pass

    def trigger_sync(self, target_peer: str):
        """Send a sync_request to target_peer with known message_ids."""
        if not target_peer or target_peer == self.node_id:
            return

        known_ids = self.queue.get_archived_message_ids()
        req_env = create_envelope(
            msg_type='sync_request',
            source=self.node_id,
            destination=target_peer,
            payload={'known_ids': known_ids},
        )

        if self.route_manager:
            route = self.route_manager.get_route(target_peer)
            if route:
                address = (route.ip, route.port)
                try:
                    self.transport.send(address, req_env)
                    return
                except Exception:
                    pass

        try:
            self.transport.send(target_peer, req_env)
        except Exception:
            pass

    def _on_transport_message(self, msg: Dict[str, Any], addr: Any):
        try:
            validate_envelope(msg)
        except Exception:
            return

        mtype = msg.get('type')
        if mtype not in ('sync_request', 'sync_response'):
            return

        dest = msg.get('destination')
        if dest and dest != self.node_id:
            return

        src = msg.get('source')
        if not src or src == self.node_id:
            return

        payload = msg.get('payload', {})

        if mtype == 'sync_request':
            remote_known_ids = set(payload.get('known_ids', []))
            local_known_ids = set(self.queue.get_archived_message_ids())
            archived = self.queue.get_all_archived_envelopes()
            now = _now_iso()
            
            # Send envelopes that requester is missing
            missing_envelopes = []
            for env in archived:
                mid = env.get('message_id')
                if not mid or mid in remote_known_ids:
                    continue
                # Check expiration
                exp = env.get('expires_at')
                if exp and exp <= now:
                    continue
                missing_envelopes.append(env)

            if missing_envelopes:
                resp_env = create_envelope(
                    msg_type='sync_response',
                    source=self.node_id,
                    destination=src,
                    payload={'envelopes': missing_envelopes},
                )
                if self.route_manager:
                    route = self.route_manager.get_route(src)
                    if route:
                        address = (route.ip, route.port)
                        try:
                            self.transport.send(address, resp_env)
                            resp_env = None
                        except Exception:
                            pass
                if resp_env:
                    try:
                        self.transport.send(addr, resp_env)
                    except Exception:
                        pass

            # If the remote peer has message IDs that we are missing, trigger a request to fetch them
            missing_for_us = remote_known_ids - local_known_ids
            if missing_for_us:
                try:
                    self.trigger_sync(src)
                except Exception:
                    pass

        elif mtype == 'sync_response':
            envelopes = payload.get('envelopes', [])
            new_synced = False
            for env in envelopes:
                mid = env.get('message_id')
                if not mid:
                    continue
                # 1. Archive envelope locally so this node has it stored and can sync it further
                self.queue.archive_message(mid, env)
                new_synced = True

                # 2. Security context verification if present
                sec = getattr(self.reliable_receiver, 'security', None)
                if sec:
                    try:
                        env = sec.open(env, reject_replay=False)
                    except Exception:
                        pass

                # 3. Deliver to app handler
                app_handler = getattr(self.reliable_receiver, 'app_handler', None)
                if app_handler:
                    payload_data = env.get('payload', {})
                    try:
                        app_handler(payload_data)
                    except Exception:
                        try:
                            app_handler(env, addr)
                        except Exception as e:
                            log.error(f"Failed to deliver synced payload {mid}: {e}")

            # Propagate newly synced messages to other connected peers
            if new_synced and self.route_manager:
                for peer_id in self.route_manager.list_routes().keys():
                    if peer_id != self.node_id and peer_id != src:
                        try:
                            self.trigger_sync(peer_id)
                        except Exception:
                            pass
