# Pre-existing core (declared)

This repository is the Open Agent Hackathon 2026 entry of **What Leaves With Them**.

The project was first built for Graph Hacks 2026 (FalkorDB, 15 to 18 October 2026) and is public at
https://github.com/retailbox-automation/what-leaves-with-them under the MIT license, with its full commit history.

The first commit of this repository imports that project **unchanged** at upstream commit
`a2cca9b` (2026-10-18). Everything in that commit is pre-existing work and is declared as such for
Open Agent Hackathon 2026, whose build window runs from 22 October 2026 00:00 UTC to 27 October 2026 23:45 UTC.
Work done during the window is in the commits after the import, and the submission lists it separately.

## How to verify the import

```
git clone https://github.com/retailbox-automation/what-leaves-with-them upstream
git -C upstream checkout a2cca9b
git clone <this repository> entry
git -C entry checkout <import commit>
diff -r --exclude=.git --exclude=PRE-EXISTING.md upstream entry   # prints nothing
```

## Manifest of the imported files (SHA-256)

| File | SHA-256 |
|---|---|
| `.dockerignore` | `b4a7464b2d7f3989aea89e3722344fdd3ceb23f5be84802d5c0dc82decc67ed7` |
| `.github/workflows/tests.yml` | `b12b238c68ed4072678bb78757ccdc08ae1b9ba829e891b6c1d8550d39c0bae7` |
| `.gitignore` | `2e432d76d8f17ddedcc5110fa16308525ef7dbc367f71da6ba9049c4b087ee9e` |
| `Dockerfile` | `788894c63f648e41ed1940acac580e4b795d971ac12b225f0d5d7dba0201833c` |
| `LICENSE` | `cb2531572305698d7b7e940bc558e549a0e9e52bb642610f1b97849202eea6f6` |
| `README.md` | `982936c077d1343b4eee296e1488515323b9da1abc2252a5f4875b04c11e30e5` |
| `bench/README.md` | `0369539cd5265a2db58d9de9fa107d43d4eec9bb475b1cfac4e1ebf5b0c2a947` |
| `bench/RESULTS.md` | `ab6c3a494109891a711788ae6bb02cf8bd79fcb1fa29a5c39e1fc5e30b96c477` |
| `bench/SENSITIVITY.md` | `e6ba376d168fbc0ff9e2579ba01eaca4d0a0d1832e6d1cfc38ef776594058f1b` |
| `bench/__init__.py` | `4e52b7a51b7e84d6773e4f601cbc6256e5e1556566a995d64d161233688748d8` |
| `bench/common.py` | `066cdd06ab7b3edc08fb96ef4dae621bb97eb947ea0c8bb963f72022529e81f0` |
| `bench/honest.py` | `7ae677f35143fea8fa43e07e59d44d6e872c991c98879dc6b1510f3f0ed2652f` |
| `bench/plain_rag.py` | `5ce33017cae34a4541177afde4e5305f288d3103618c5f345c53fc8b3619bc1e` |
| `bench/report.py` | `05ff65554a493fe44a7011b3218e9fe84c9545e75185d1a8e501a9ad8ddc040b` |
| `bench/results/agent-thinking-default/RESULTS.md` | `1334dc3e7611b85ef3a2e1c938cf6a11d3ad018895d149f89d3e594b6e9e882e` |
| `bench/results/agent-thinking-default/honest-agent-seed42.jsonl` | `aa33662d937d68c64a5ec923a8e43f5526969691dbc86c9b70b7b2f20fba1c82` |
| `bench/results/agent-thinking-default/summary.json` | `ec5de06e3de7865e260b3a81415a6677ae66ac6d3fd37168bb1e6b08014d68df` |
| `bench/results/falkordb-6.0.2/RESULTS.md` | `c4e9c0cf68b18738631039f6ecaaa6c12d8e36dfb5c752c8b0ae13b984848756` |
| `bench/results/falkordb-6.0.2/honest-agent-seed42.jsonl` | `6deb41251bfb0656baa2ba8effb4450dd92be5e8e1427ff9d99da1a4cda39863` |
| `bench/results/falkordb-6.0.2/honest-router-seed42.jsonl` | `2c0c00e406457e16bb21df137d6924a105a2ed260de59da134fa1a7fa077475a` |
| `bench/results/falkordb-6.0.2/summary.json` | `3562a2876767d915c3658b53ed16190c08863473542ffbe2d9e7f44ada8c9c90` |
| `bench/results/honest-agent-seed42.jsonl` | `e3097cfe57e9a526e1c65cd011a97e1cc0d19665b5af4599ccf08e0de2d8e19a` |
| `bench/results/honest-router-seed42.jsonl` | `8ed2ec852754d5a6a6a21cbfdaf3099deeef011079037d912134472b96d9b65c` |
| `bench/results/plain-rag-k16/RESULTS.md` | `3691c3c19d25a993da74910325443ff2c38f316ea28484804685e1b89010a091` |
| `bench/results/plain-rag-k16/plain-rag-seed42.jsonl` | `5ad0c289abf14f0d33182387eedab13359eb1035966b5576afc1773ff30be02a` |
| `bench/results/plain-rag-k16/summary.json` | `ef1cca78512487bed5953bb984e7d38cb0368966898d09c0f0b94f3da0e9700b` |
| `bench/results/plain-rag-lines-k16/RESULTS.md` | `9f15a46c54c4f9fbd856e5b92b3215be077b740aa3367adc511921b27f79cd79` |
| `bench/results/plain-rag-lines-k16/plain-rag-seed42.jsonl` | `c3b5183bcf6f3c00482a6ba69b0ee11961c52f2db0bb6a21732faf01788068a5` |
| `bench/results/plain-rag-lines-k16/summary.json` | `eb7e1f69f4a008897bb5e874664141c33937a69586a4553641ca42aa9c730af7` |
| `bench/results/plain-rag-lines-k8/RESULTS.md` | `855b5495248bb1a94b7e5ae6a88a389a00a018832040825c083a3fa2471c633a` |
| `bench/results/plain-rag-lines-k8/plain-rag-seed42.jsonl` | `942fc1f997f35f3275d07a4cb262ce65939f49db77cf6b01c5ddf065f497bc51` |
| `bench/results/plain-rag-lines-k8/summary.json` | `63b42ed31fc97c761b3fba90264b5632974a578f17649ed16a958ada356afe91` |
| `bench/results/plain-rag-seed42.jsonl` | `865bb324506b35da203dccfc9cfb2c0873d7e2ac38632150332149e755138d68` |
| `bench/results/summary.json` | `1058c4c40c4dd82f476f7685ebcbadf9f737f1b3e51d7bf7519da3d0c71499ce` |
| `bench/run.py` | `c2fea4d5a80c2d12e96e6f582e0bf940bc4756fa20d5b9a5a4d37be25f1bf72a` |
| `bench/run_all.sh` | `fd7a7f90c58880d05fc911d7625ab5906c8092d24c10a2c025acad03cd41b752` |
| `bench/score.py` | `ece9edb9d43a181d589de9da0e055c456a4952ded4323314fb5af65550ac2449` |
| `data/MANIFEST.md` | `9994ac354c5d37259c17795ae124735b8e852c52cef9f45cde75a03720075ffb` |
| `deploy/bootstrap.py` | `ebb3e63ce8ba6fd8dfcc70c4546d9374a16ddd81593fb12d4e1ccc0c850d12e2` |
| `deploy/index.html` | `a8da18068b813648534117ca09b04031cf780d9b48229aad20aa7db43d982cb3` |
| `deploy/redeploy.sh` | `4b4dcaefa4fb5cbd7a12e9f8a8935b72aab1b7923a4ac55ab5ff39ca48507a22` |
| `deploy/start.sh` | `240ebe79f2cac08b9405426238eff25ec4edcdcaa665e710916b7a9c5051e37e` |
| `deploy/zeabur-falkordb.yaml` | `eec9e0a5abac6e0a91a4d6dc51c76d47c925cddefd21d680f2832571272d1c68` |
| `docs/ARCHITECTURE.md` | `269cff8f8f2b52594631d5845770c958144278b46f5594256762bd2483620c41` |
| `docs/DEMO-N5.md` | `5c4e1ad6f40279f0992513a58a0146f6d895462ebc913ac988d663ca82445837` |
| `docs/DEPLOY.md` | `36ab7133e87bda5f682ab3774d84056a49f660758b8933c75684dad0a61c0504` |
| `docs/MCP.md` | `b3c206d78561104a485a981fa094447be7a46e9a3af5c92746e5a607999f3125` |
| `docs/blog/img/after-handoff.png` | `5cf3b1864f907c3ffd69b38ed9bd834f3cd5ce51e3706fc70eb189bb4cad206d` |
| `docs/blog/img/cover-remove.png` | `614c6593ada162fa9a3b802e8b514a4829fa79941fc1b77267b2dfb6cfe70b19` |
| `docs/blog/img/handoff-14-days.gif` | `a1d404d5dd1df4a42eccf6999a43f8fcb935c5c59495dd6973fa2f0e7a0a1367` |
| `docs/blog/img/heatmap.png` | `8660222645bccc0d3413978ddf05caebaee1e220add7034ab9a786a472002e3c` |
| `docs/blog/img/remove-oskar.gif` | `2509b8301a905d62acc9a87027f8a80c1f9bf04d6d509a2c6e99ce682e3e902a` |
| `docs/blog/img/three-people.png` | `eb191b1d37297cb8ef0f6ebd37823faaefb3c60f7d4ff754a1c81071f021e2d4` |
| `docs/design-sketch.md` | `f89219f69af43f861f639ec37cfa0221499a1f13796c81cec0e5e41f3ae11486` |
| `docs/sponsor-feedback.md` | `3ff5bd97da8593dffb16630d124a933f6f3a4bf3c2c93bc0af9a2347691b12df` |
| `docs/ui/panel-shots/01-landing.png` | `c062ec2435e4586047d7aecfec7ef1efca58f42f0e0bda35aa65cbc17d8dedf0` |
| `docs/ui/panel-shots/02-oskar-depth1.png` | `eeb821c3a8ab1ccc0618601a6b681b960c49349d6f5bda903fc7574f1b44d947` |
| `docs/ui/panel-shots/03-oskar-depth4.png` | `9a30e04bda95cb257b017942478dbb091818b0e783772d4a4e8a55ece9cd168a` |
| `docs/ui/panel-shots/04-oskar-depth4-hover-sources.png` | `5cf764ef8a67461c8de33abd1a1bdcb33c415d8cb70f4226aec8f83a7e754bcb` |
| `docs/ui/panel-shots/05-oskar-removed.png` | `b44920bca74e30bfac10b9b0c25d67c6d7355e863a7a5098e77f623437a1343e` |
| `docs/ui/panel-shots/06-oskar-handoff-game.png` | `a5176bc066f28ad6f208cec54581701b2d6d7370a36af9d78c3f5fb147621d10` |
| `docs/ui/panel-shots/07-oskar-game-two-answers.png` | `8caad40641a6159d1641cc99e917c94605010dc94de271173e0e0fbf82a44ae3` |
| `docs/ui/panel-shots/08-oskar-game-all-answers.png` | `91b31fe28febd281923e48559e15f1940ee7a366dbfe49f617d86be2ab523a24` |
| `docs/ui/panel-shots/09-oskar-after-handoff.png` | `8d530e635737c400c8d6fad7404af173bd5a783dd0768452d60841d4e1e1f0bb` |
| `docs/ui/panel-shots/10-oskar-show-cypher.png` | `14317a19e52e4aace5a7cf9aa71ed9638cc0a416f333d124c312e8ab3f4b87ce` |
| `docs/ui/panel-shots/11-luca-removed.png` | `a2eea8b84d9e7437c8748bd350860d2a622f47ddd94aa18b9933f6a4df32d198` |
| `docs/ui/panel-shots/12-luca-after-handoff.png` | `e04f25932042780a4d2cce48265175b6062b87853946084b961c6dec4d166b97` |
| `docs/ui/panel-shots/13-zara-removed.png` | `90c86244e345582b01192bce00a20d44426fa8b6e0ce779d6f66de8d5431f8cb` |
| `docs/ui/panel-shots/14-zara-after-handoff.png` | `67ba25a1a1d15aa44dfd9a127f85ff69f79e81f5b7b71bdc29f92f909239258c` |
| `docs/ui/panel-shots/15-1366x768-depth4.png` | `986227147bc13c6699a28105b31786199c10dc8602572c68d0c5005a56fcbd54` |
| `docs/ui/panel-shots/16-1366x768-after-handoff.png` | `640b067601739100bd5497ff77509225304bbdf61ec84275d5674bd834032bf4` |
| `docs/ui/panel-shots/17-phone-390x844.png` | `13142c7930e0ce05a42724b375b85fd5bf4a2a16d8dabcf59a649c7560fe3329` |
| `docs/ui/panel-shots/18-recorded-answers-after-handoff.png` | `484c7020fa7287c379f74e57ea8f8d108bfb9f2e4b73751ff41ef415b7706ce6` |
| `examples/departure_demo.py` | `72c1f3ddea73e42a068fa5167470f9e9e1e82958da56b624cfa2cc714af58f7b` |
| `examples/two_agents.py` | `b4ed5e53875378bd62c9352170b6ca4a9e6a0f607ebf079da1c1a32caac24410` |
| `honest_memory/__init__.py` | `f88251c90150b51f300019a949cf512cc09c2a2541297fe3c0e1f5103ca1bc10` |
| `honest_memory/agent.py` | `a595dc4c074abf9d99831b8fa875cd18450f6d5a3bc10409038c7fa3fbb755b9` |
| `honest_memory/answer.py` | `9b5b0c79f9c0712af73bbb7a118428e62f6bf9562f3ffad80313eeb81df0b6d5` |
| `honest_memory/api.py` | `5123f8ce893fc5104b62b91f8c4291a6a44f941c40e267a3f926ce7e37349a5c` |
| `honest_memory/corpus/__init__.py` | `b0cc5669f026f97e09380e8646f05d709e6dd47d918b1a928e8d07f43c715104` |
| `honest_memory/corpus/__main__.py` | `e51d98ffdad019d7696aa2bc174b174db779a793dab80c55af3d4e91f6e4ebe1` |
| `honest_memory/corpus/generator.py` | `c332d71d8000e038d41f040e773bddec4c51a50f0421180cb2ea6825c056948b` |
| `honest_memory/corpus/templates.py` | `700315c426236082b515f6f44923aacedaae752ceaa6994e3df25d90b5c80aca` |
| `honest_memory/departure.py` | `b7c01dfe83a4252119ef869149f310f85f33d2343c797c7db2a8505cfa648aa8` |
| `honest_memory/extract.py` | `d33a8faad0eecea04948a738266fe77921645083938ff56274dad5b24740abed` |
| `honest_memory/gate.py` | `971ab75f5816e95edbb79593627744f262b9514581f889415aae857433dc7a0a` |
| `honest_memory/ingest.py` | `7fa60f6ef442ddb9361adc866980a1948ef1fb2faebbb73c528f363547e00476` |
| `honest_memory/mcp_server.py` | `fbd0f2e5c94cf920dd49d0a8e43bf844cd1cdfc5e9ac0fef350e0c343e4d4b71` |
| `honest_memory/names.py` | `70c6474fb87592a7819916d85c06ca1e6d69675f593b93477544e618b720320b` |
| `honest_memory/router.py` | `c2eef0be43371f08bdfa740027c5a79ac9341327a2e495d877f8a55cb2657c7e` |
| `honest_memory/schema.py` | `83ae799dd83b798bd132944f2add99fce3c9f0418916482e6078a8d8fd662241` |
| `honest_memory/snapshot.py` | `fbd62431bc885c2cfcd60d5f31f9da6e161bcf831f0791860328b452d9e9ffd8` |
| `honest_memory/store.py` | `a8a915c13f2c87875817f20ea2e43675f33d2092cdaca1569fbee0c65f98274a` |
| `honest_memory/tools.py` | `ea4b1cea0f6765ee16c71dd0210f35b2a690ddc8d1d06d4c20d9867319f8d8f1` |
| `pyproject.toml` | `0901e6dd4348f2ceb2e1b41d8c37e6ca284a08bbae06b758e3a6d363d9fd3b6d` |
| `queries/01-what-leaves-depth-1.cypher` | `370cba0eed8ccecaf25a1e5947cb15410a47a4c9697a4c29fb503673fbedaa2a` |
| `queries/02-what-leaves-depth-4.cypher` | `5b0fbd2f74c23b350de24fc3f11f7f57cf9625432fb00ac26390582228dcdb67` |
| `queries/03-single-source.cypher` | `791383a19cb57e93976f865996848f908cdcd3855345769b70546ad468053eb5` |
| `queries/04-without.cypher` | `3d0134a5aed9b947c2f3d9a4fb18235061e193a7d14cc9fd37664f56ad011dd7` |
| `queries/05-bridges.cypher` | `f339b6983ad849397a2a94660e6bfccd396d58bb28ef85302e836435290de41d` |
| `queries/06-successors.cypher` | `a69c02bcefc9116e7315be9b18edbb9f52401f0af28077a836dea1cc0565c747` |
| `queries/07-promises.cypher` | `c2a420ae1e15373a9abb848a7c44d119f10b6cc08bfde0de4e01d4a50a7c4467` |
| `queries/08-handoff-plan.cypher` | `2b55d14fe1921371b649102482dd450fb35a713b0fd6c9c2ef9baf651c85a216` |
| `redteam/REPORT.md` | `d85da2e8a18627f1d09a6c4ffb30c5dc8c89a105d7af807f0a0b3fa5a74dc99e` |
| `redteam/attacks.jsonl` | `1b5188c3041e0c1e399fe4b4e764556cb6b99ca6366ea2ed5bca7b775d8eef98` |
| `redteam/controls.jsonl` | `457044630c2f9a847deb78978d18b7ec9f0b5e01512661beb9311b2bc591471b` |
| `redteam/first-run-4.22.0.json` | `b70a1c9c7e2c1f4c2098319bdffc58b354de77359cfd85365ee49cab509026e2` |
| `redteam/first-run-6.0.2.json` | `d6b97c88fd43c0f2549fedba3dfcbf421a83a1999c0ccf09ae4ac6cf96e8c465` |
| `redteam/first-run-close-4.22.0.json` | `182331d727506e8862999e7f8d29a067b7dd1a8a521d56801a2a9c0ca6c8d1d0` |
| `redteam/first-run-close-6.0.2.json` | `bef60ceef29d6fd0dc330918d8ebb7f342cd1d20f992d590fc1201f7a6baecbe` |
| `redteam/fixes.json` | `289ce043ae37b982dda70bfbf77a2a2eac02108a1f76f11ce1139c96f2f5e80f` |
| `redteam/notes.md` | `c6a753b9265685644cdf4c6107a9eb5752c2168a1dfbfc6bed31f57670adef74` |
| `redteam/results-4.22.0.json` | `cf89048b2b9708d03307b8aa445cab1df18209662f9f81a3db5d3f253c5a1cf3` |
| `redteam/results-6.0.2.json` | `d35ca792039ca9776f4ec0730abf813c43bf632a6b44e8af37668087fc58935c` |
| `redteam/run.py` | `95265806f195c1155644599bad6d83788fe548a70b90cc68d9dc47eac6a5064a` |
| `scripts/capture_panel_fixtures.py` | `f9341f14513986770571abceb1c5d3c5f98f876766457f32584a4c82321aee6b` |
| `scripts/export_queries.py` | `339dfcd79246eda7c1a0355ae1519b38496effc7b3f675328d06eb78e4dda350` |
| `spike/betweenness_repro.py` | `f10ae91efb2e55c12b69989713345b52debb978e451a59c29d31a4298688dd00` |
| `spike/graphiti_clone_repro.py` | `bd36117ce57d93a06b9660a9ecf29f5cae7c7d28fdd1465d231afab09799d0a0` |
| `spike/spike_cloud.py` | `943d0ea72c5f7015bd869c8d33c431565c9e9000364d3e9a8bdba034eb593382` |
| `spike/spike_local.py` | `c62d43d64ad232cbc3be305722c478335b81de90b3adaf4a03618ca655234d82` |
| `spike/with_where_repro.py` | `af6ae9d3b4c4c5ef6594cb33d1f85492b36170fb95e812132c4ba25e2f81b0e8` |
| `tests/conftest.py` | `841dc6f0d075233d7ddb7f0e09185f6ad6f2b0791e008fe99723637276da8018` |
| `tests/fixtures.py` | `df8108ee5dbdb90a03dcee83c655dea5f85d33ef0de77d39ccadd0139ba1a21b` |
| `tests/test_agent.py` | `fda5d0d32285b5eb8737ee2b6a86705475cf986f5bdd1fd8081716c08b6dfc75` |
| `tests/test_answer.py` | `09f152feac7396578468e30f758b2d7aad21ee9ccdeca893f8b41db2f5eecf91` |
| `tests/test_api.py` | `03a9a40ab071548eba425f8586505ec295d1baccf34671a3836fb213381c5353` |
| `tests/test_bench_score.py` | `04e0a8d1a905199b13b115015b764332f3c109557e6acb4d95527611c6afbbf5` |
| `tests/test_corpus.py` | `f43e945fbb66ec7217e7d529b6951b6ca13567fc71dc77188c190e29049fe8f5` |
| `tests/test_departure.py` | `53e51c998c32c9391274580d68286415790510a686d1a1300d86f9bc58565626` |
| `tests/test_extract.py` | `de9fb67348c01885e956c8bdf240acd0d22e249fddbe39f5e5ce107506d63c38` |
| `tests/test_gate.py` | `a9f139bc93e30cdd56990629d3766fd00bc742cb81b32232ca62784eb3a3b9d6` |
| `tests/test_gate_close.py` | `e8c139d845589116f5431b408a7681d312fde06fcb5f121e6814d6c21961b9ed` |
| `tests/test_gate_close_redteam.py` | `db25defb7020159d65db38b20724a2a0ac3cadcd222d9ccc63a08fe4282e87c0` |
| `tests/test_ingest.py` | `fa0ecc5ce33e4018ae952559785c280cb92e89e09a8955abca527cc0a8dd87e6` |
| `tests/test_mcp.py` | `c6c78723b2bf29eee0009c16c36fa7fe3b00abfa7b745b42596c58a72bece454` |
| `tests/test_queries.py` | `be98f50e462f798eabf240c714a1ff7202b8cb1787b97ee3c2f0bc87947b8ba1` |
| `tests/test_router.py` | `aed8db2db8dac8eb275e6c3ca81b7a78069a0b945b850a6d740a9e8a105c711e` |
| `tests/test_snapshot.py` | `98f23f180e43af5c5c250f1473fae777edce47901c4c462ffffabb0efef5c1f1` |
| `tests/test_store.py` | `61955f9afbec7ada924e8ed518df600ccb704df168cda70ba17f75df37db3d88` |
| `tests/test_store_records.py` | `f81c4fe3dd19d00202d3f861942f561159bb613baeee39e2b770cfdbf4c02c26` |
| `tests/test_tools.py` | `d63452662237810a75f24f5c64ac58b15183cefb482d82411b1e73b45f797f7d` |
| `tests/test_two_agents.py` | `c65002c3478a5eb2913fc5dadda81da09c2844375ee50e81d6c7003648e0eaca` |
| `tests/test_write_tools.py` | `76d1e57b002856af946e53eab93c97f8a55ac89909c9436c5f72573efd35a789` |
| `web/app.js` | `eee8126b5d6855b26cf09b498de1e88f32fc886d50ed390a810f11a0fb40535d` |
| `web/fixtures/common.json` | `c3525be02a582bea15876d42dfb24fafd390be6dddaa1b38f473d2ebf167a5c9` |
| `web/fixtures/people/aiko-rademacher.json` | `60bb3c3005c994ce4ec363fbb86f8a2e14ce4fe8679ef4154581c7c839e440fe` |
| `web/fixtures/people/lars-thorne.json` | `75ae828ef2a6c1bbeaaf01af8c62b357caa59c9c234aecf2e150054ad90133d1` |
| `web/fixtures/people/luca-ostrander.json` | `12a1435f97d2c495dc452d6ea2b2c58b357a8934544afd3a1a97fad233a689d8` |
| `web/fixtures/people/mateo-sandoval.json` | `6e9ae76bc7f334f232d3d007618a542cf59bd293f65cdee0f87e8c785f1c2c86` |
| `web/fixtures/people/omar-fenwick.json` | `278b1cb2639adc0f60c60deadb0404eeb4e12590c5415f4183330f609e4016d3` |
| `web/fixtures/people/oskar-alvarez.json` | `32018f8192220e7cc5832c97eee6aef17964d1a1c6d778339755b929199e3cdf` |
| `web/fixtures/people/pavel-hollis.json` | `a67e90386111c5d033e1f128c8703ba8c2eebfa51c32dd58bec9c0cb85fb15a2` |
| `web/fixtures/people/priya-farrow.json` | `40b904520a63692cccc545590051dc130e35669f5d43ff8a8ba69828061a7c5a` |
| `web/fixtures/people/sana-petrov.json` | `dae1d30bccdb573438130189d8a58c966ea4457841d6c566f9c0305c0379029a` |
| `web/fixtures/people/selma-marsh.json` | `5c1495556bf0c58d3a1c16e2dca58ccfadadce0c35474523d94fa17ee312cb19` |
| `web/fixtures/people/tomas-kessler.json` | `6020c6acee781a549a06715b6ace08f32a6d586df51c612deb5df03aa14059d5` |
| `web/fixtures/people/viktor-hartwell.json` | `09ef3150d52f0b83d3a01c07f2afabcea967c0a62b9c245b9d11e3c68ff8430b` |
| `web/fixtures/people/yuki-moreau.json` | `280ab6f9422f733ac6798c0ddb1c83c36e21d01d10e7314eee1589b258100bdc` |
| `web/fixtures/people/zara-lowell.json` | `006f0a7ecf107a5732aef9fb303baf88609d9932d2d52cd25553ed03562cd336` |
| `web/index.html` | `5e285b196c1ce3a7b5aa668bd84652a58c15df02cf49cf2fb2349fa88c2a2c3c` |
| `web/style.css` | `49076d19c93b88f522f2c355b2778d111bd0f6971d1837c0b74354719f3e4907` |
| `web/vendor/LICENSE-cytoscape.txt` | `eb319c6e6f233607f71e8e2f450391751883cfc0eeb3ca7ef574c13d1d9c2203` |
| `web/vendor/README.md` | `53f2f02a25206838537ef7d3e3ca838aea6eb52f8105723483c346e1ff54258a` |
| `web/vendor/cytoscape-3.34.3.min.js` | `5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91` |

165 files.
