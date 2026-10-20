import unittest

from SolutionAll.SolutionAll import Solution


class TestSolution(unittest.TestCase):
    def setUp(self):
        self.solution = Solution()

    def test_find_maximum_elegance(self):
        self.assertEqual(
            self.solution.findMaximumElegance([[3, 2], [5, 1], [10, 1]], 2),
            17,
        )
        self.assertEqual(
            self.solution.findMaximumElegance(
                [[3, 1], [3, 1], [2, 2], [5, 3]], 3
            ),
            19,
        )

    def test_constrained_lis(self):
        self.assertEqual(
            self.solution.lengthOfLIS([4, 2, 1, 4, 3, 4, 5, 8, 15], 3), 5
        )
        self.assertEqual(
            self.solution.lengthOfLIS([7, 4, 5, 1, 8, 12, 4, 7], 5), 4
        )
        self.assertEqual(self.solution.lengthOfLIS([1, 5], 1), 1)

    def test_music_playlists(self):
        self.assertEqual(self.solution.numMusicPlaylists(3, 3, 1), 6)
        self.assertEqual(self.solution.numMusicPlaylists(2, 3, 0), 6)
        self.assertEqual(self.solution.numMusicPlaylists(2, 3, 1), 2)

    def test_visible_points(self):
        self.assertEqual(
            self.solution.visiblePoints([[2, 1], [2, 2], [3, 3]], 90, [1, 1]),
            3,
        )
        self.assertEqual(
            self.solution.visiblePoints([[1, 0], [2, 1]], 13, [1, 1]), 1
        )
        self.assertEqual(
            self.solution.visiblePoints(
                [[2, 0], [2, 0], [2, 2], [0, 0]], 45, [0, 0]
            ),
            4,
        )

    def test_min_days(self):
        self.assertEqual(
            self.solution.minDays(
                [[0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]]
            ),
            2,
        )
        self.assertEqual(self.solution.minDays([[1, 1]]), 2)
        self.assertEqual(self.solution.minDays([[0]]), 0)
        self.assertEqual(self.solution.minDays([[1, 0, 1]]), 0)

    def test_friend_requests(self):
        self.assertEqual(
            self.solution.friendRequests(3, [[0, 1]], [[0, 2], [2, 1]]),
            [True, False],
        )
        self.assertEqual(
            self.solution.friendRequests(
                5,
                [[0, 1], [1, 2], [2, 3]],
                [[0, 4], [1, 2], [3, 1], [3, 4]],
            ),
            [True, False, True, False],
        )


if __name__ == "__main__":
    unittest.main()
