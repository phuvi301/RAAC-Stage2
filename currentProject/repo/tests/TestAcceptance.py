import itertools
import math
import unittest

from SolutionAll.SolutionAll import Solution


class TestAcceptance(unittest.TestCase):
    def setUp(self):
        self.s = Solution()

    def test_maximum_elegance_against_exhaustive_oracle(self):
        items = [[8, 1], [7, 1], [5, 2], [4, 3], [1, 2]]
        for k in range(1, len(items) + 1):
            expected = max(
                sum(items[i][0] for i in choice)
                + len({items[i][1] for i in choice}) ** 2
                for choice in itertools.combinations(range(len(items)), k)
            )
            self.assertEqual(self.s.findMaximumElegance([x[:] for x in items], k), expected)

    def test_constrained_lis_against_exhaustive_oracle(self):
        for nums in itertools.product(range(1, 5), repeat=5):
            for k in (1, 2, 4):
                expected = 0
                for mask in range(1 << len(nums)):
                    seq = [nums[i] for i in range(len(nums)) if mask >> i & 1]
                    if all(a < b and b - a <= k for a, b in zip(seq, seq[1:])):
                        expected = max(expected, len(seq))
                self.assertEqual(self.s.lengthOfLIS(list(nums), k), expected)

    def test_music_playlists_against_enumeration_and_modulus(self):
        for n in range(1, 4):
            for goal in range(n, 6):
                for k in range(n):
                    expected = 0
                    for playlist in itertools.product(range(n), repeat=goal):
                        if len(set(playlist)) != n:
                            continue
                        if all(song not in playlist[max(0, i - k):i]
                               for i, song in enumerate(playlist)):
                            expected += 1
                    self.assertEqual(self.s.numMusicPlaylists(n, goal, k), expected)
        value = self.s.numMusicPlaylists(100, 100, 0)
        self.assertEqual(value, math.factorial(100) % 1000000007)

    def test_visible_points_wrap_boundary_duplicates_and_location(self):
        points = [[2, 0], [2, 0], [2, 2], [0, 2], [1, 1], [1, 1]]
        self.assertEqual(self.s.visiblePoints(points, 45, [1, 1]), 4)
        # Directions 179 and -179 degrees must share a two-degree wraparound window.
        self.assertEqual(self.s.visiblePoints([[-99, 2], [-99, 0], [2, 1]], 3, [1, 1]), 2)
        # Both angular boundaries are inclusive.
        self.assertEqual(self.s.visiblePoints([[2, 0], [2, 2]], 90, [1, 1]), 2)

    def test_min_days_against_exhaustive_small_grid_oracle(self):
        def islands(grid):
            seen = set()
            total = 0
            for r in range(len(grid)):
                for c in range(len(grid[0])):
                    if not grid[r][c] or (r, c) in seen:
                        continue
                    total += 1
                    stack = [(r, c)]
                    seen.add((r, c))
                    while stack:
                        x, y = stack.pop()
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            p = (x + dx, y + dy)
                            if (0 <= p[0] < len(grid) and 0 <= p[1] < len(grid[0])
                                    and grid[p[0]][p[1]] and p not in seen):
                                seen.add(p)
                                stack.append(p)
            return total

        for bits in range(1 << 6):
            grid = [[(bits >> (r * 3 + c)) & 1 for c in range(3)] for r in range(2)]
            if islands(grid) != 1:
                expected = 0
            else:
                expected = 2
                for r in range(2):
                    for c in range(3):
                        if grid[r][c]:
                            candidate = [row[:] for row in grid]
                            candidate[r][c] = 0
                            if islands(candidate) != 1:
                                expected = 1
            self.assertEqual(self.s.minDays([row[:] for row in grid]), expected)

    def test_friend_requests_component_oracle(self):
        n = 6
        restrictions = [[0, 1], [2, 3], [0, 5]]
        requests = [[0, 2], [1, 3], [2, 4], [3, 5], [4, 0], [0, 2]]
        components = [{i} for i in range(n)]
        expected = []
        for a, b in requests:
            ca = next(group for group in components if a in group)
            cb = next(group for group in components if b in group)
            merged = ca | cb
            allowed = all(not ({x, y} <= merged) for x, y in restrictions)
            expected.append(allowed)
            if allowed and ca is not cb:
                components.remove(ca)
                components.remove(cb)
                components.append(merged)
        self.assertEqual(self.s.friendRequests(n, restrictions, requests), expected)


if __name__ == "__main__":
    unittest.main()
