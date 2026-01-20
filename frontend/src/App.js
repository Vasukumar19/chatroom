import React, { useEffect, useState } from 'react';
import './App.css';

function App() {
  const [messages, setMessages] = useState([]);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const interval = setInterval(() => {
      fetch('http://localhost:5001/messages')
        .then(res => res.json())
        .then(data => {
          console.log(data);
          setMessages(data);
        })
        .catch(err => console.error('Error fetching messages:', err));
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const sendMessage = async () => {
    if (message.trim() === '') return;

    try {
      await fetch('http://localhost:5001/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message }),
      });
      setMessage('');
    } catch (err) {
      console.error('Error sending message:', err);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="app-container">
      <div className="chat-container">
        
        {/* HEADER SECTION */}
        <div className="header">
          <div className="icon-container">
            <svg 
              className="alert-icon" 
              width="48" 
              height="48" 
              viewBox="0 0 24 24" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
              <line x1="12" y1="9" x2="12" y2="13"></line>
              <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
          </div>
          <div className="header-text">
            <h1>DisasterNet (No Internet Required)</h1>
            <p>Emergency Communication Network</p>
          </div>
        </div>

        {/* MESSAGES SECTION */}
        <div className="messages-section">
          {Array.isArray(messages) ? (
            messages.map((msg, idx) => (
              <div key={idx} className="message-bubble">
                {msg}
              </div>
            ))
          ) : (
            <div className="error-message">No messages available</div>
          )}
        </div>

        {/* INPUT SECTION */}
        <div className="input-section">
          <input
            type="text"
            placeholder="Type your message here..."
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyPress={handleKeyPress}
            className="message-input"
          />
          <button onClick={sendMessage} className="send-button">
            <svg 
              width="32" 
              height="32" 
              viewBox="0 0 24 24" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2"
            >
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </div>
      </div>

      {/* FOOTER INFO SECTION */}
      <div className="footer-container">
        <div className="footer-content">
          <p className="footer-title">
            Created By -{" "}
            <a
              href="https://github.com/AbhinavXJ"
              target="_blank"
              rel="noopener noreferrer"
            >
              Abhinav Jha
            </a>
          </p>

          <p className="footer-description">
            Frontend part of the <strong>DisasterNet</strong> project — made to
            communicate in places <span className="highlight">without the NEED OF INTERNET</span>!
          </p>

          <a
            href="https://github.com/AbhinavXJ/DisasterNet"
            target="_blank"
            rel="noopener noreferrer"
            className="footer-link"
          >
            View full project (frontend + backend) on GitHub →
          </a>
        </div>
      </div>
    </div>
  );
}

export default App;