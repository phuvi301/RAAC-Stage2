# Profile môi trường theo dataset

Đã kiểm tra manifest tại đúng base commit của 120 task (setup.py, setup.cfg, pyproject.toml và tox.ini khi tồn tại). Cấu hình hợp lệ không đồng nghĩa đã chạy thành công. Kết quả Docker thực tế được ghi riêng trong báo cáo batch.

| Repo | Task | Profile |
|---|---:|---|
| astropy/astropy | 12 | configs/repos/astropy.yaml |
| django/django | 13 | configs/repos/django.yaml |
| matplotlib/matplotlib | 12 | configs/repos/matplotlib.yaml |
| mwaskom/seaborn | 2 | configs/repos/seaborn.yaml |
| pallets/flask | 1 | configs/repos/flask.yaml |
| psf/requests | 8 | configs/repos/requests.yaml |
| pydata/xarray | 12 | configs/repos/xarray.yaml |
| pylint-dev/pylint | 10 | configs/repos/pylint.yaml |
| pytest-dev/pytest | 12 | configs/repos/pytest.yaml |
| scikit-learn/scikit-learn | 12 | configs/repos/scikit-learn.yaml |
| sphinx-doc/sphinx | 13 | configs/repos/sphinx.yaml |
| sympy/sympy | 13 | configs/repos/sympy.yaml |

Requests giữ profile riêng đã có. Astropy, Matplotlib, scikit-learn và Xarray có override cho các thế hệ dependency/Python khác nhau; Django dùng SQLite và runner gốc; Sphinx có override cho manifest mới yêu cầu Python >=3.9. Các giới hạn network và tài nguyên không bị nới để làm test pass.

## Manifest đã kiểm tra

| Task | Commit | File hiện diện |
|---|---|---|
| astropy__astropy-14507 | `a3f4ae6cd24d5ecdf49f213d77b3513dd509a06c` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-7335 | `732d89c2940156bdc0e200bb36dc38b5e424bcba` | setup.py, setup.cfg |
| astropy__astropy-8340 | `1a4462d72eb03f30dc83a879b1dd57aac8b2c18b` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-8706 | `a85a0747c54bac75e9c3b2fe436b105ea029d6cf` | setup.py, setup.cfg |
| astropy__astropy-12906 | `d16bfe05a744909de4b27f5875fe0d4ed41ce607` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-13009 | `298ccb478e6bf092953bca67a3d29dc6c35f6752` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-13355 | `6500928dc0e57be8f06d1162eacc3ba5e2eff692` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-13488 | `0df94ff7097961e92fd7812036a24b145bc13ca8` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-14355 | `fa4e8d1cd279acf9b24560813c8652494ccd5922` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-14363 | `7269fa3e33e8d02485a647da91a5a2a60a06af61` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-14523 | `c0a24c1dc957a3b565294213f435fefb2ec99714` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| astropy__astropy-14581 | `80c3854a5f4f4a6ab86c03d9db7854767fcd83c1` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-23740 | `ce69e34bd646558bb44ea92cecfd98b345a0b3e0` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-28373 | `c84b91b7603e488f7171fdff8f08368ef3d6b856` | setup.py, setup.cfg, tox.ini |
| django__django-28477 | `321ecb40f4da842926e1bc07e11df4aabe53ca4b` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-29834 | `14d026cccb144c6877294ba4cd4e03ebf0842498` | setup.py, setup.cfg, tox.ini |
| django__django-29998 | `29c126bb349526b5f1cd78facbe9f25906f18563` | setup.py, setup.cfg, tox.ini |
| django__django-30289 | `e245046bb6e8b32360aa48b8a41fb7050f0fc730` | setup.py, setup.cfg, tox.ini |
| django__django-30691 | `003bb34b218adb23d1a7e67932a6ba9b3c4dcc81` | setup.py, setup.cfg, tox.ini |
| django__django-31723 | `2a55431a5678af52f669ffe7dff3dd0bd21727f8` | setup.py, setup.cfg, tox.ini |
| django__django-32690 | `06fd4df41afb5aa1d681b853c3c08d8c688ca3a5` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-33413 | `694cf458f16b8d340a3195244196980b2dec34fd` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-33480 | `71e7c8e73712419626f1c2b6ec036e8559a2d667` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-33552 | `859a87d873ce7152af73ab851653b4e1c3ffea4c` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| django__django-34272 | `39f83765e12b0e5d260b7939fc3fe281d879b279` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-5087 | `7fdf772201e4c9bafbc16dfac23b5472d6a53fa2` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-12926 | `5ca694b38d861c0e24cd8743753427dda839b90b` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-13982 | `a3e2897bfaf9eaac1d6649da535c4e721c89fa69` | setup.py, tox.ini |
| matplotlib__matplotlib-20665 | `6786f437df54ca7780a047203cbcfaa1db8dc542` | setup.py, tox.ini |
| matplotlib__matplotlib-22640 | `a2a1b0a11b993fe5f8fab64b6161e99243a6393c` | setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-24097 | `493d608e39d32a67173c23a7bbc47d6bfedcef61` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-24127 | `af39f1edffcd828f05cfdd04f2e59506bb4a27bc` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-24386 | `8f0003ae902952372824c9917975fb372c026a42` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-24821 | `5ec2bd279729ff534719b8bf238dbbca907b93c5` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-25165 | `f8ffce6d44127d4ea7d6491262ab30046b03294b` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-25300 | `430fb1db88843300fb4baae3edc499bbfe073b0c` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| matplotlib__matplotlib-25329 | `66ba515e671638971bd11a34cff12c107a437e0b` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| mwaskom__seaborn-2967 | `54cab15bdacfaa05a88fbc5502a5b322d99f148e` | pyproject.toml, setup.cfg |
| mwaskom__seaborn-3174 | `22cdfb0c93f8ec78492d87edb810f10cb7f57a31` | pyproject.toml, setup.cfg |
| pallets__flask-5010 | `7ee9ceb71e868944a46e1ff00b506772a53a4f1d` | pyproject.toml, tox.ini |
| psf__requests-1051 | `22623bd8c265b78b161542663ee980738441c307` | setup.py |
| psf__requests-1723 | `1ba83c47ce7b177efe90d5f51f7760680f72eda0` | setup.py |
| psf__requests-1765 | `847735553aeda6e6633f2b32e14ba14ba86887a4` | setup.py, setup.cfg |
| psf__requests-1920 | `3c88e520da24ae6f736929a750876e7654accc3d` | setup.py, setup.cfg |
| psf__requests-2316 | `091991be0da19de9108dbe5e3752917fea3d7fdc` | setup.py, setup.cfg |
| psf__requests-2930 | `5f7a3a74aab1625c2bb65f643197ee885e3da576` | setup.py, setup.cfg |
| psf__requests-5367 | `39d0fdd9096f7dceccbc8f82e1eda7dd64717a8e` | setup.py, setup.cfg, tox.ini |
| psf__requests-6027 | `0192aac24123735b3eaf9b08df46429bb770c283` | setup.py, setup.cfg, tox.ini |
| pydata__xarray-3150 | `118f4d996e7711c9aced916e6049af9f28d5ec66` | setup.py, setup.cfg |
| pydata__xarray-3676 | `ef6e6a7b86f8479b9a1fecf15ad5b88a2326b31e` | setup.py, setup.cfg |
| pydata__xarray-3992 | `8cc34cb412ba89ebca12fc84f76a9e452628f1bc` | setup.py, setup.cfg |
| pydata__xarray-4049 | `a64cf2d5476e7bbda099b34c40b7be1880dbd39a` | setup.py, setup.cfg |
| pydata__xarray-4074 | `19b088636eb7d3f65ab7a1046ac672e0689371d8` | setup.py, setup.cfg |
| pydata__xarray-4682 | `d3b6aa6d8b997df115a53c001d00222a0f92f63a` | pyproject.toml, setup.py, setup.cfg |
| pydata__xarray-4354 | `e05fddea852d08fc0845f954b79deb9e9f9ff883` | setup.py, setup.cfg |
| pydata__xarray-4627 | `a41edc7bf5302f2ea327943c0c48c532b12009bc` | setup.py, setup.cfg |
| pydata__xarray-6444 | `851dadeb0338403e5021c3fbe80cbc9127ee672d` | pyproject.toml, setup.py, setup.cfg |
| pydata__xarray-6597 | `6bb2b855498b5c68d7cca8cceb710365d58e6048` | pyproject.toml, setup.py, setup.cfg |
| pydata__xarray-7036 | `45c0a114e2b7b27b83c9618bc05b36afac82183c` | pyproject.toml, setup.py, setup.cfg |
| pydata__xarray-7220 | `3aa75c8d00a4a2d4acf10d80f76b937cadb666b7` | pyproject.toml, setup.py, setup.cfg |
| pylint-dev__pylint-1548 | `99589b08de8c5a2c6cc61e13a37420a868c80599` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-3878 | `1d1619ef913b99b06647d2030bddff4800abdf63` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-4603 | `1e55ae64624d28c5fe8b63ad7979880ee2e6ef3f` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-4901 | `40cc2ffd7887959157aaf469e09585ec2be7f528` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-6385 | `754b487f4d892e3d4872b6fc7468a71db4e31c13` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-6471 | `273a8b25620467c1e5686aa8d2a1dbb8c02c78d0` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-6902 | `ca80f03a43bc39e4cc2c67dc99817b3c9f13b8a6` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-6964 | `3c5eca2ded3dd2b59ebaf23eb289453b5d2930f0` | setup.py, setup.cfg, tox.ini |
| pylint-dev__pylint-7229 | `1f8c4d9eb185c16a2c1d881c054f015e1c2eb334` | pyproject.toml, setup.cfg, tox.ini |
| pylint-dev__pylint-7231 | `684a1d6aa0a6791e20078bc524f97c8906332390` | pyproject.toml, setup.cfg, tox.ini |
| pytest-dev__pytest-5786 | `955e54221008aba577ecbaefa15679f6777d3bf8` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-5806 | `8aba863a634f40560e25055d179220f0eefabe9a` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-5819 | `73c5b7f4b11a81e971f7d1bb18072e06a87060f4` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-6194 | `e856638ba086fcf5bebf1bebea32d5cf78de87b4` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-7126 | `5e7f1ab4bf58e473e5d7f878eb2b499d7deabd29` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-7322 | `19ad5889353c7f5f2b65cc2acd346b7a9e95dfcd` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-7392 | `e6e300e729dd33956e5448d8be9a0b1540b4e53a` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-7517 | `41d211c24a6781843b174379d6d6538f5c17adb9` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-7792 | `3c1534944cbd34e8a41bc9e76818018fadefc9a1` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-7981 | `a7e38c5c61928033a2dc1915cbee8caa8544a4d0` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-9877 | `aa55975c7d3f6c9f6d7f68accc41bb7cadf0eb9a` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| pytest-dev__pytest-10060 | `da9a2b584eb7a6c7e924b2621ed0ddaeca0a7bea` | pyproject.toml, setup.py, setup.cfg, tox.ini |
| scikit-learn__scikit-learn-9287 | `3eacf948e0f95ef957862568d87ce082f378e186` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-10284 | `b90661d6a46aa3619d3eec94d5281f5888add501` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-12650 | `d360ffa7c5896a91ae498b3fb9cf464464ce8f34` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-12972 | `a7b8b9e9e16d4e15fabda5ae615086c2e1c47d8a` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-13110 | `9f0b959a8c9195d1b6e203f08b698e052b426ca9` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-14059 | `a5743ed36fbd3fbc8e351bdab16561fbfca7dfa1` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-14709 | `4b6273b87442a4437d8b3873ea3022ae163f4fdf` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-14893 | `fdbaa58acbead5a254f2e6d597dc1ab3b947f4c6` | setup.py, setup.cfg |
| scikit-learn__scikit-learn-24860 | `f9a1cf072da9d7375d6c2163f68a6038b13b310f` | pyproject.toml, setup.py, setup.cfg |
| scikit-learn__scikit-learn-25844 | `e3d1f9ac39e4bf0f31430e779acc50fb05fe1b64` | pyproject.toml, setup.py, setup.cfg |
| scikit-learn__scikit-learn-25957 | `10dbc142bd17ccf7bd38eec2ac04b52ce0d1009e` | pyproject.toml, setup.py, setup.cfg |
| scikit-learn__scikit-learn-26306 | `586f4318ffcdfbd9a1093f35ad43e81983740b66` | pyproject.toml, setup.py, setup.cfg |
| sphinx-doc__sphinx-4258 | `27ac10de04697e2372d31db5548e56a7c6d9265d` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-7294 | `2e506c5ab457cba743bb47eb5b8c8eb9dd51d23d` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-8034 | `5e6da19f0e44a0ae83944fb6ce18f18f781e1a6e` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-8438 | `f35d2a6cc726f97d0e859ca7a0e1729f7da8a6c8` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-741 | `dd1615c59dc6fff633e27dbb3861f2d27e1fb976` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-8618 | `21698c14461d27933864d73e6fba568a154e83b3` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-9218 | `876fa81e0a038cda466925b85ccf6c5452e0f685` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-9224 | `567ff22716ac258b9edd2c1711d766b440ac0b11` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-1874 | `06107f838c28ab6ca6bfc2cc208e15997fcb2146` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-9445 | `939c7bb7ff7c53a4d27df067cea637540f0e1dad` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-10318 | `31eba1a76dd485dc633cae48227b46879eda5df4` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-10104 | `cab2d93076d0cca7c53fac885f927dde3e2a5fec` | setup.py, setup.cfg, tox.ini |
| sphinx-doc__sphinx-10678 | `6cb783c0024a873722952a67ebb9f41771c8eb6d` | pyproject.toml, tox.ini |
| sympy__sympy-7132 | `c935e1d106743efd5bf0705fbeedbd18fadff4dc` | setup.py |
| sympy__sympy-12465 | `aa9780761ad8c3c0f68beeef3a0ce5caac9e100b` | setup.py |
| sympy__sympy-13055 | `7121bdf1facdd90d05b6994b4c2e5b2865a4638a` | setup.py |
| sympy__sympy-13076 | `30379ea6e225e37833a764ac2da7b7fadf5fe374` | setup.py |
| sympy__sympy-13830 | `7b127bdf71a36d85216315f80c1b54d22b060818` | setup.py |
| sympy__sympy-14237 | `9986b38181cdd556a3f3411e553864f11912244e` | setup.py |
| sympy__sympy-16432 | `6fd65310fa3167b9626c38a5487e171ca407d988` | setup.py |
| sympy__sympy-16884 | `c50643a49811e9fe2f4851adff4313ad46f7325e` | setup.py |
| sympy__sympy-18212 | `ba80d1e493f21431b4bf729b3e0452cd47eb9566` | setup.py, setup.cfg |
| sympy__sympy-17624 | `58e78209c8577b9890e957b624466e5beed7eb08` | setup.py |
| sympy__sympy-18755 | `70381f282f2d9d039da860e391fe51649df2779d` | setup.py, setup.cfg |
| sympy__sympy-19608 | `63f8f465d48559fecb4e4bf3c48b75bf15a3e0ef` | setup.py, setup.cfg |
| sympy__sympy-20796 | `e11d3fed782146eebbffdc9ced0364b223b84b6c` | setup.py, setup.cfg |
