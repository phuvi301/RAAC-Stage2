import unittest
from collections import Counter
from itertools import combinations

from SolutionAll.SolutionAll import Solution


class TestAcceptance(unittest.TestCase):
    def setUp(self):
        self.s = Solution()

    def test_acceptance_examples(self):
        s = self.s
        self.assertEqual((s.numberOfWays("abcd", "cdab", 2), s.numberOfWays("ababab", "ababab", 1)), (2, 2))
        self.assertEqual(s.longestCommonSubpath(5, [[0,1,2,3,4],[2,3,4],[4,0,1,2,3]]), 2)
        self.assertEqual(s.longestCommonSubpath(3, [[0],[1],[2]]), 0)
        for n, sums in [(3,[-3,-2,-1,0,0,1,2,3]), (2,[0,0,0,0])]:
            got=s.recoverArray(n,sums)
            self.assertIs(type(got), list); self.assertEqual(len(got), n)
            actual=[]
            for mask in range(1<<n): actual.append(sum(got[i] for i in range(n) if mask>>i&1))
            self.assertEqual(Counter(actual), Counter(sums))
        self.assertEqual(s.findGoodStrings(2,"aa","da","b"),51); self.assertEqual(s.findGoodStrings(8,"leetcode","leetgoes","leet"),0)
        self.assertEqual(s.countPalindromicSubsequences("bccb"),6)
        self.assertEqual([s.minimumIncompatibility([1,2,1,4],2),s.minimumIncompatibility([6,3,8,1,3,1,2,2],4),s.minimumIncompatibility([5,3,3,6,3,3],3)],[4,6,-1])
        self.assertEqual(s.kthSmallestProduct([2,5],[3,4],2),8); self.assertEqual(s.kthSmallestProduct([-4,-2,0,3],[2,4],6),0)
        self.assertEqual(s.minimumFinishTime([[2,3],[3,4]],5,4),21); self.assertEqual(s.minimumFinishTime([[1,10],[2,2],[3,4]],6,5),25)
        for words, expected_len in [(["alex","loves","leetcode"],17),(["catg","ctaagt","gcta","ttca","atgcatc"],16)]:
            got=s.shortestSuperstring(words); self.assertIs(type(got),str); self.assertEqual(len(got),expected_len); self.assertTrue(all(w in got for w in words))
        self.assertEqual([s.mergeStones([3,2,4,1],2),s.mergeStones([3,2,4,1],3),s.mergeStones([3,5,1,2,6],3)],[20,-1,25])
        self.assertEqual(s.connectTwoGroups([[15,96],[36,2]]),17); self.assertEqual(s.connectTwoGroups([[1,3,5],[4,1,1],[1,5,3]]),4)
        self.assertEqual(s.numberOfSequence(5,[0,4]),4); self.assertEqual(s.numberOfSequence(4,[1]),3)
        self.assertEqual(s.strangePrinter("aaabbb"),2); self.assertEqual(s.strangePrinter("aba"),2)
        self.assertEqual(s.countSubgraphsForEachDiameter(4,[[1,2],[2,3],[2,4]]),[3,4,0]); self.assertEqual(s.countSubgraphsForEachDiameter(3,[[1,2],[2,3]]),[2,1])
        self.assertEqual(s.findMaximumElegance([[3,2],[5,1],[10,1]],2),17); self.assertEqual(s.findMaximumElegance([[3,1],[3,1],[2,2],[5,3]],3),19)
        self.assertEqual([s.lengthOfLIS([4,2,1,4,3,4,5,8,15],3),s.lengthOfLIS([7,4,5,1,8,12,4,7],5),s.lengthOfLIS([1,5],1)],[5,4,1])
        self.assertEqual([s.numMusicPlaylists(3,3,1),s.numMusicPlaylists(2,3,0),s.numMusicPlaylists(2,3,1)],[6,6,2])
        self.assertEqual(s.visiblePoints([[2,1],[2,2],[3,3]],90,[1,1]),3); self.assertEqual(s.visiblePoints([[1,0],[2,1]],13,[1,1]),1)
        self.assertEqual(s.visiblePoints([[0,0],[0,0],[1,0],[0,1]],90,[0,0]),4)
        self.assertEqual(s.minDays([[0,1,1,0],[0,1,1,0],[0,0,0,0]]),2); self.assertEqual(s.minDays([[1,1]]),2)
        self.assertEqual(s.minDays([[0,0],[0,0]]),0); self.assertEqual(s.minDays([[1,0,1]]),0)
        self.assertEqual(s.friendRequests(3,[[0,1]],[[0,2],[2,1]]),[True,False])
        self.assertEqual(s.friendRequests(5,[[0,1],[1,2],[2,3]],[[0,4],[1,2],[3,1],[3,4]]),[True,False,True,False])


if __name__ == '__main__':
    unittest.main()
