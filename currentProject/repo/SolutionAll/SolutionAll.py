from array import array
from bisect import bisect_left, bisect_right
from collections import Counter
from functools import lru_cache
import heapq
import math


class Solution(object):
    MOD = 1000000007

    def numberOfWays(self, s, t, k):
        n = len(s)
        pattern = t + "\0" + s + s[:-1]
        pi = [0] * len(pattern)
        matches = 0
        for i in range(1, len(pattern)):
            j = pi[i - 1]
            while j and pattern[i] != pattern[j]:
                j = pi[j - 1]
            if pattern[i] == pattern[j]:
                j += 1
            pi[i] = j
            if i > n and j == n:
                matches += 1
        if not matches:
            return 0
        def mul(a, b):
            return [[sum(a[i][x] * b[x][j] for x in range(2)) % self.MOD
                     for j in range(2)] for i in range(2)]
        matrix = [[0, n - 1], [1, n - 2]]
        result = [[1, 0], [0, 1]]
        while k:
            if k & 1:
                result = mul(result, matrix)
            matrix = mul(matrix, matrix)
            k //= 2
        same, different = result[0][0], result[1][0]
        if s == t:
            return (same + (matches - 1) * different) % self.MOD
        return matches * different % self.MOD

    def longestCommonSubpath(self, n, paths):
        paths.sort(key=len); mod=(1<<61)-1; base=100003
        def ok(L):
            common=None
            for p in paths:
                h=0; pw=pow(base,L,mod); cur=set()
                for i,x in enumerate(p):
                    h=(h*base+x+1)%mod
                    if i>=L: h=(h-(p[i-L]+1)*pw)%mod
                    if i>=L-1: cur.add(h)
                common=cur if common is None else common&cur
                if not common:return False
            return True
        lo,hi=0,len(paths[0])+1
        while lo+1<hi:
            mid=(lo+hi)//2
            if ok(mid):lo=mid
            else:hi=mid
        return lo

    def recoverArray(self, n, sums):
        sums=sorted(sums)
        def rec(a,d):
            if d==0:return []
            x=a[1]-a[0]; cnt=Counter(a); left=[]; right=[]
            for v in a:
                if cnt[v]:
                    left.append(v); right.append(v+x); cnt[v]-=1; cnt[v+x]-=1
            if 0 in left:
                z=rec(left,d-1)
                if z is not None:return [x]+z
            if 0 in right:
                z=rec(right,d-1)
                if z is not None:return [-x]+z
            return None
        return rec(sums,n)

    def findGoodStrings(self, n, s1, s2, evil):
        m=len(evil); pi=[0]*m
        for i in range(1,m):
            j=pi[i-1]
            while j and evil[i]!=evil[j]:j=pi[j-1]
            if evil[i]==evil[j]:j+=1
            pi[i]=j
        trans=[[0]*26 for _ in range(m)]
        for j in range(m):
            for c in range(26):
                ch=chr(97+c); q=j
                while q and evil[q]!=ch:q=pi[q-1]
                if evil[q]==ch:q+=1
                trans[j][c]=q
        def upto(bound):
            dp={(0,True):1}
            for i in range(n):
                nd={}
                for (q,tight),v in dp.items():
                    top=ord(bound[i])-97 if tight else 25
                    for c in range(top+1):
                        nq=trans[q][c]
                        if nq<m:
                            key=(nq,tight and c==top); nd[key]=(nd.get(key,0)+v)%self.MOD
                dp=nd
            return sum(dp.values())%self.MOD
        def dec(s):
            a=list(s); i=len(a)-1
            while a[i]=='a':a[i]='z';i-=1
            if i<0:return None
            a[i]=chr(ord(a[i])-1);return ''.join(a)
        p=dec(s1)
        return (upto(s2)-(upto(p) if p else 0))%self.MOD

    def countPalindromicSubsequences(self, s):
        n=len(s); dp=[[0]*n for _ in range(n)]
        for i in range(n):dp[i][i]=1
        for L in range(2,n+1):
            for i in range(n-L+1):
                j=i+L-1
                if s[i]!=s[j]: dp[i][j]=(dp[i+1][j]+dp[i][j-1]-dp[i+1][j-1])%self.MOD
                else:
                    l=i+1;r=j-1
                    while l<=r and s[l]!=s[i]:l+=1
                    while l<=r and s[r]!=s[i]:r-=1
                    mid=dp[i+1][j-1] if L>2 else 0
                    if l>r:dp[i][j]=2*mid+2
                    elif l==r:dp[i][j]=2*mid+1
                    else:dp[i][j]=2*mid-(dp[l+1][r-1] if l+1<=r-1 else 0)
                    dp[i][j]%=self.MOD
        return dp[0][-1]

    def minimumIncompatibility(self, nums, k):
        n=len(nums); size=n//k
        if max(Counter(nums).values())>k:return -1
        vals={}
        for mask in range(1<<n):
            if mask.bit_count()==size:
                a=[nums[i] for i in range(n) if mask>>i&1]
                if len(set(a))==size:vals[mask]=max(a)-min(a)
        inf=10**9; dp=[inf]*(1<<n);dp[0]=0
        for mask in range(1<<n):
            if dp[mask]==inf:continue
            first=next((i for i in range(n) if not mask>>i&1),None)
            if first is None:continue
            for sub,v in vals.items():
                if sub>>first&1 and not sub&mask:dp[mask|sub]=min(dp[mask|sub],dp[mask]+v)
        return dp[-1] if dp[-1]<inf else -1

    def kthSmallestProduct(self, a, b, k):
        def count(x):
            total=0
            for v in a:
                if v>0:total+=bisect_right(b,x//v)
                elif v==0:total+=len(b) if x>=0 else 0
                else: total+=len(b)-bisect_left(b,math.ceil(x/v))
            return total
        lo,hi=-10**10-1,10**10
        while lo+1<hi:
            mid=(lo+hi)//2
            if count(mid)>=k:hi=mid
            else:lo=mid
        return hi

    def minimumFinishTime(self, tires, changeTime, numLaps):
        best=[10**30]*(numLaps+1)
        for f,r in tires:
            cur=f; total=0
            for j in range(1,numLaps+1):
                total+=cur;best[j]=min(best[j],total)
                cur*=r
                if cur>f+changeTime:break
        dp=[0]+[10**30]*numLaps
        for i in range(1,numLaps+1):
            for j in range(1,i+1):dp[i]=min(dp[i],dp[i-j]+best[j]+(changeTime if i>j else 0))
        return dp[-1]

    def shortestSuperstring(self, words):
        n=len(words); ov=[[0]*n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                for z in range(min(len(words[i]),len(words[j])),0,-1):
                    if words[i][-z:]==words[j][:z]:ov[i][j]=z;break
        dp=[[None]*n for _ in range(1<<n)]
        for i,w in enumerate(words):dp[1<<i][i]=w
        for mask in range(1<<n):
            for i in range(n):
                if dp[mask][i] is not None:
                    for j in range(n):
                        if not mask>>j&1:
                            v=dp[mask][i]+words[j][ov[i][j]:]; nm=mask|1<<j
                            if dp[nm][j] is None or len(v)<len(dp[nm][j]):dp[nm][j]=v
        return min((x for x in dp[-1] if x is not None),key=len)

    def mergeStones(self, stones, k):
        n=len(stones)
        if (n-1)%(k-1):return -1
        pre=[0]
        for x in stones:pre.append(pre[-1]+x)
        dp=[[0]*n for _ in range(n)]
        for L in range(k,n+1):
            for i in range(n-L+1):
                j=i+L-1;dp[i][j]=min(dp[i][m]+dp[m+1][j] for m in range(i,j,k-1))
                if (L-1)%(k-1)==0:dp[i][j]+=pre[j+1]-pre[i]
        return dp[0][-1]

    def connectTwoGroups(self, cost):
        m=len(cost[0]); inf=10**9;dp=[inf]*(1<<m);dp[0]=0
        for row in cost:
            nd=[inf]*(1<<m)
            for mask,v in enumerate(dp):
                for j,c in enumerate(row):nd[mask|1<<j]=min(nd[mask|1<<j],v+c)
            dp=nd
        extra=[min(row[j] for row in cost) for j in range(m)]
        return min(v+sum(extra[j] for j in range(m) if not mask>>j&1) for mask,v in enumerate(dp))

    def numberOfSequence(self, n, sick):
        gaps=[sick[0]]+[sick[i]-sick[i-1]-1 for i in range(1,len(sick))]+[n-1-sick[-1]]; h=n-len(sick)
        ans=math.factorial(h)
        for g in gaps:ans=ans*pow(math.factorial(g),self.MOD-2,self.MOD)%self.MOD
        ans=ans*pow(2,sum(max(0,g-1) for g in gaps[1:-1]),self.MOD)%self.MOD
        return ans

    def strangePrinter(self, s):
        s=''.join(c for i,c in enumerate(s) if i==0 or c!=s[i-1]);n=len(s);dp=[[0]*n for _ in range(n)]
        for i in range(n-1,-1,-1):
            dp[i][i]=1
            for j in range(i+1,n):
                dp[i][j]=dp[i+1][j]+1
                for z in range(i+1,j+1):
                    if s[z]==s[i]:dp[i][j]=min(dp[i][j],(dp[i+1][z-1] if z>i+1 else 0)+dp[z][j])
        return dp[0][-1]

    def countSubgraphsForEachDiameter(self, n, edges):
        g=[[] for _ in range(n)]
        for a,b in edges:a-=1;b-=1;g[a].append(b);g[b].append(a)
        ans=[0]*(n-1)
        for mask in range(1,1<<n):
            nodes=[i for i in range(n) if mask>>i&1]
            if len(nodes)<2:continue
            def bfs(src):
                q=[src];dist={src:0}
                for u in q:
                    for v in g[u]:
                        if mask>>v&1 and v not in dist:dist[v]=dist[u]+1;q.append(v)
                return dist
            d=bfs(nodes[0])
            if len(d)==len(nodes):ans[max(max(bfs(x).values()) for x in nodes)-1]+=1
        return ans

    def findMaximumElegance(self, items, k):
        items.sort(reverse=True); total=0;seen=set();dup=[]
        for p,c in items[:k]:
            total+=p
            if c in seen:heapq.heappush(dup,p)
            seen.add(c)
        ans=total+len(seen)**2
        for p,c in items[k:]:
            if c not in seen and dup:
                total+=p-heapq.heappop(dup);seen.add(c);ans=max(ans,total+len(seen)**2)
        return ans

    def lengthOfLIS(self, nums, k):
        size=1
        while size<=max(nums):size*=2
        tree=[0]*(2*size)
        def query(l,r):
            l+=size;r+=size;v=0
            while l<r:
                if l&1:v=max(v,tree[l]);l+=1
                if r&1:r-=1;v=max(v,tree[r])
                l//=2;r//=2
            return v
        for x in nums:
            v=query(max(0,x-k),x)+1;i=x+size;tree[i]=max(tree[i],v)
            while i>1:i//=2;tree[i]=max(tree[2*i],tree[2*i+1])
        return tree[1]

    def numMusicPlaylists(self, n, goal, k):
        dp=[0]*(n+1);dp[0]=1
        for _ in range(goal):
            nd=[0]*(n+1)
            for used in range(n+1):
                if used<n:nd[used+1]+=dp[used]*(n-used)
                if used>k:nd[used]+=dp[used]*(used-k)
            dp=[x%self.MOD for x in nd]
        return dp[n]

    def visiblePoints(self, points, angle, location):
        a=[];same=0;x,y=location
        for p,q in points:
            if p==x and q==y:same+=1
            else:a.append(math.atan2(q-y,p-x))
        a.sort(); ext=a+[v+2*math.pi for v in a];j=0;best=0;w=math.radians(angle)+1e-12
        for i,v in enumerate(ext[:len(a)]):
            j=max(j,i)
            while j<len(ext) and ext[j]-v<=w:j+=1
            best=max(best,j-i)
        return same+min(best,len(a))

    def minDays(self, grid):
        m=len(grid);n=len(grid[0])
        def islands():
            seen=set();cnt=0
            for i in range(m):
                for j in range(n):
                    if grid[i][j] and (i,j) not in seen:
                        cnt+=1;stack=[(i,j)];seen.add((i,j))
                        for x,y in stack:
                            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                                z=(x+dx,y+dy)
                                if 0<=z[0]<m and 0<=z[1]<n and grid[z[0]][z[1]] and z not in seen:seen.add(z);stack.append(z)
            return cnt
        if islands()!=1:return 0
        for i in range(m):
            for j in range(n):
                if grid[i][j]:
                    grid[i][j]=0
                    if islands()!=1:grid[i][j]=1;return 1
                    grid[i][j]=1
        return 2

    def friendRequests(self, n, restrictions, requests):
        p=list(range(n))
        def find(x):
            while p[x]!=x:p[x]=p[p[x]];x=p[x]
            return x
        out=[]
        for a,b in requests:
            x,y=find(a),find(b);ok=True
            if x!=y:
                for u,v in restrictions:
                    if {find(u),find(v)}=={x,y}:ok=False;break
                if ok:p[y]=x
            out.append(ok)
        return out
