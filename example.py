containedBoxes = [[1,2],[3],[],[],[],[]] 
initialBoxes = [0]
status = [1,0,1,0]
candies = [7,5,4,100]
keys = [[],[],[1],[]]
from collections import deque
from typing import List

class Solution:
    def maxCandies(self, status: List[int], candies: List[int],
                   keys: List[List[int]], containedBoxes: List[List[int]],
                   initialBoxes: List[int]) -> int:

        q = deque(initialBoxes)
        seen = set(initialBoxes)
        total = 0

        while q:
            box = q.popleft()

            if status[box] == 0:
                continue

            # collect candies
            total += candies[box]
            candies[box] = 0  # avoid double count

            # use keys
            for k in keys[box]:
                status[k] = 1
                print(k)
                if k in seen:
                    q.append(k)

            # add contained boxes
            for b in containedBoxes[box]:
                if b not in seen:
                    seen.add(b)
                    q.append(b)

        return total
ob=Solution()
print(ob.maxCandies(status,candies,keys,containedBoxes,initialBoxes))
#
