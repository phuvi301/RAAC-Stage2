from array import array
from bisect import bisect_left, bisect_right
from collections import Counter
from functools import lru_cache
import heapq
import math


class Solution(object):
    def findMaximumElegance(self, items, k):
        items.sort(reverse=True)
        total = 0
        categories = set()
        duplicates = []
        for profit, category in items[:k]:
            total += profit
            if category in categories:
                heapq.heappush(duplicates, profit)
            else:
                categories.add(category)

        answer = total + len(categories) ** 2
        for profit, category in items[k:]:
            if not duplicates:
                break
            if category in categories:
                continue
            total += profit - heapq.heappop(duplicates)
            categories.add(category)
            answer = max(answer, total + len(categories) ** 2)
        return answer

    def lengthOfLIS(self, nums, k):
        size = max(nums) + 1
        tree = [0] * (2 * size)

        def query(left, right):
            result = 0
            left += size
            right += size
            while left < right:
                if left & 1:
                    result = max(result, tree[left])
                    left += 1
                if right & 1:
                    right -= 1
                    result = max(result, tree[right])
                left //= 2
                right //= 2
            return result

        def update(index, value):
            index += size
            tree[index] = max(tree[index], value)
            index //= 2
            while index:
                tree[index] = max(tree[2 * index], tree[2 * index + 1])
                index //= 2

        answer = 0
        for value in nums:
            length = query(max(0, value - k), value) + 1
            update(value, length)
            answer = max(answer, length)
        return answer

    def numMusicPlaylists(self, n, goal, k):
        modulus = 1000000007
        dp = [[0] * (n + 1) for _ in range(goal + 1)]
        dp[0][0] = 1
        for length in range(goal):
            for used in range(n + 1):
                count = dp[length][used]
                if not count:
                    continue
                if used < n:
                    dp[length + 1][used + 1] += count * (n - used)
                if used > k:
                    dp[length + 1][used] += count * (used - k)
                dp[length + 1][used] %= modulus
                if used < n:
                    dp[length + 1][used + 1] %= modulus
        return dp[goal][n]

    def visiblePoints(self, points, angle, location):
        same = 0
        directions = []
        x0, y0 = location
        for x, y in points:
            if x == x0 and y == y0:
                same += 1
            else:
                directions.append(math.degrees(math.atan2(y - y0, x - x0)))
        directions.sort()
        count = len(directions)
        extended = directions + [value + 360.0 for value in directions]
        best = 0
        right = 0
        for left in range(count):
            right = max(right, left)
            while (right < left + count and
                   extended[right] - extended[left] <= angle + 1e-10):
                right += 1
            best = max(best, right - left)
        return best + same

    def minDays(self, grid):
        rows, columns = len(grid), len(grid[0])

        def islands():
            seen = set()
            count = 0
            for row in range(rows):
                for column in range(columns):
                    if not grid[row][column] or (row, column) in seen:
                        continue
                    count += 1
                    if count > 1:
                        return count
                    stack = [(row, column)]
                    seen.add((row, column))
                    while stack:
                        current_row, current_column = stack.pop()
                        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            nr, nc = current_row + dr, current_column + dc
                            if (0 <= nr < rows and 0 <= nc < columns and
                                    grid[nr][nc] and (nr, nc) not in seen):
                                seen.add((nr, nc))
                                stack.append((nr, nc))
            return count

        if islands() != 1:
            return 0
        for row in range(rows):
            for column in range(columns):
                if grid[row][column]:
                    grid[row][column] = 0
                    disconnected = islands() != 1
                    grid[row][column] = 1
                    if disconnected:
                        return 1
        return 2

    def friendRequests(self, n, restrictions, requests):
        parent = list(range(n))
        rank = [0] * n

        def find(person):
            while parent[person] != person:
                parent[person] = parent[parent[person]]
                person = parent[person]
            return person

        def union(first, second):
            first, second = find(first), find(second)
            if first == second:
                return
            if rank[first] < rank[second]:
                first, second = second, first
            parent[second] = first
            if rank[first] == rank[second]:
                rank[first] += 1

        answer = []
        for first, second in requests:
            root_first, root_second = find(first), find(second)
            allowed = True
            if root_first != root_second:
                for restricted_first, restricted_second in restrictions:
                    left, right = find(restricted_first), find(restricted_second)
                    if ((left == root_first and right == root_second) or
                            (left == root_second and right == root_first)):
                        allowed = False
                        break
            if allowed:
                union(root_first, root_second)
            answer.append(allowed)
        return answer
