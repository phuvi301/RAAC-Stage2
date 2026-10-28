"""Topology graph in the trace viewer: endpoint, served assets and per-node progress logic."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from raac.collaboration.topologies import CHAIN
from raac.common.trace_web import create_trace_server, present_event

ASSETS = Path(__file__).resolve().parents[3] / "src/raac/common/trace_assets"


@contextmanager
def running_server(path):
    server = create_trace_server(path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class GraphEndpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "llm.jsonl"
        self.path.write_text(json.dumps({"event": "run_started", "decision": {"topology": "chain"}}) + "\n", encoding="utf-8")

    def test_topology_endpoint_mirrors_declared_layout_and_assets_are_served(self):
        with running_server(self.path) as url:
            with urlopen(url + "/api/topology", timeout=3) as response:
                data = json.load(response)
            self.assertEqual(data["topology_id"], "chain")
            self.assertEqual(data["version"], CHAIN.version)
            self.assertEqual([step["node_id"] for step in data["steps"]], [step.node_id for step in CHAIN.steps])
            self.assertEqual([tuple(edge) for edge in data["edges"]], list(CHAIN.edges))
            self.assertEqual((data["entry_node"], data["exit_node"]), (CHAIN.entry_node, CHAIN.exit_node))
            with urlopen(url + "/graph.mjs", timeout=3) as response:
                self.assertIn("text/javascript", response.headers["Content-Type"])
                self.assertIn(b"export function summarize", response.read())
            with urlopen(url + "/", timeout=3) as response:
                page = response.read().decode()
            self.assertIn('id="flow-canvas"', page)
            self.assertIn('id="flow-detail"', page)
            with self.assertRaises(HTTPError) as error:
                urlopen(url + "/api/topology?name=unknown", timeout=3)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_run_started_row_names_its_topology(self):
        row = present_event({"event": "run_started", "decision": {"topology": "chain", "max_rounds": 3}}, 0)
        self.assertEqual(row["topology"], "chain")


@unittest.skipUnless(shutil.which("node"), "node is needed to run the viewer's ES modules")
class GraphLogicTests(unittest.TestCase):
    PROGRAM = """
import {layout, summarize} from %(module)s;
const topology = %(topology)s;
const ev = (event, agent, extra = {}) => ({event, agent, tone: "info", technical: false, title: event, summary: "", ...extra});
const life = (agent, round, done = true, extra = {}) => [ev("agent_started", agent, {round}), ...(done ? [ev("agent_completed", agent, {round, summary: agent + " xong", ...extra})] : [])];
const scenarios = %(scenarios)s;
const out = {};
out.layout = (() => {const l = layout(topology); return {nodes: Object.fromEntries(l.nodes.map(n => [n.node_id, {x: n.x, y: n.y, row: n.row}])), back: [...l.back]};})();
for (const [name, build] of Object.entries(scenarios)) {
  const s = summarize(topology, eval(build));
  out[name] = {status: Object.fromEntries([...s.nodes].map(([id, n]) => [id, n.status])),
    runs: Object.fromEntries([...s.nodes].map(([id, n]) => [id, n.runs])),
    activity: Object.fromEntries([...s.nodes].map(([id, n]) => [id, n.activity?.title || null])),
    edges: s.edges.filter(e => e.traversed || e.active).map(e => `${e.from}>${e.to}${e.active ? "*" : ""}`)};
}
console.log(JSON.stringify(out));
"""

    def run_node(self, scenarios):
        topology = {"steps": [{"node_id": s.node_id, "role": s.role} for s in CHAIN.steps],
                    "edges": [list(e) for e in CHAIN.edges], "entry_node": CHAIN.entry_node, "exit_node": CHAIN.exit_node}
        program = self.PROGRAM % {"module": json.dumps((ASSETS / "graph.mjs").as_uri()),
                                  "topology": json.dumps(topology), "scenarios": json.dumps(scenarios)}
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "probe.mjs"
            script.write_text(program, encoding="utf-8")
            done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_layout_keeps_main_path_in_a_row_and_hangs_limit_below_it(self):
        result = self.run_node({})["layout"]
        nodes = result["nodes"]
        main = ["environment", "analyst", "planner", "coder", "tester", "check", "reviewer", "result"]
        main = main[main.index(CHAIN.entry_node):]
        self.assertEqual({nodes[i]["row"] for i in main}, {0})
        self.assertEqual([nodes[i]["x"] for i in main], sorted(nodes[i]["x"] for i in main))
        self.assertEqual(nodes["limit"]["row"], 1)
        self.assertTrue(nodes["check"]["x"] < nodes["limit"]["x"] < nodes["reviewer"]["x"])
        self.assertEqual(result["back"], ["limit>coder"])

    def test_progress_states_current_activity_and_retry_loop(self):
        scenarios = {
            "fresh": "[]",
            "running_tool": "[ev('run_started','mas'), ...life('environment',1), ...life('analyst',1), ...life('planner',1), "
                            "ev('agent_started','coder',{round:1}), ev('tool_call_started','coder',{tool_name:'run_command',tool_call_id:'t1'})]",
            "waiting_llm": "[...life('environment',1), ev('agent_started','analyst'), ev('llm_request','analyst',{turn:2})]",
            "retry": "[...life('environment',1), ...life('analyst',1), ...life('planner',1), ...life('coder',1), ...life('tester',1), "
                     "ev('agent_started','coder',{round:2})]",
            "failed": "[...life('environment',1), ev('agent_started','analyst'), ev('agent_failed','analyst',{tone:'error',summary:'timeout'})]",
            "blocked_env": "[...life('environment',1,true,{tone:'error'}), ev('agent_completed','environment',{tone:'error'})]",
            "finished": "[...life('environment',1), ...life('analyst',1), ...life('planner',1), ...life('coder',1), ...life('tester',1), "
                        "...life('reviewer',1), ev('run_completed','mas')]",
            "interrupted": "[ev('agent_started','coder'), ev('run_completed','mas',{tone:'error'})]",
        }
        data = self.run_node(scenarios)
        self.assertTrue(all(status == "idle" for status in data["fresh"]["status"].values()))
        self.assertEqual(data["fresh"]["edges"], [])
        running = data["running_tool"]
        self.assertEqual(running["status"]["coder"], "running")
        self.assertEqual(running["activity"]["coder"], "Đang chạy tool · run_command")
        self.assertIn("planner>coder*", running["edges"])
        self.assertEqual(data["waiting_llm"]["activity"]["analyst"], "Đang chờ LLM trả lời (lượt #2)")
        retry = data["retry"]
        self.assertEqual((retry["status"]["coder"], retry["runs"]["coder"]), ("running", 2))
        self.assertEqual((retry["status"]["check"], retry["status"]["limit"]), ("done", "done"))
        # The loop-back edge is the live one while Coder reruns; its first-pass edge is history.
        self.assertIn("limit>coder*", retry["edges"])
        self.assertIn("planner>coder", retry["edges"])
        self.assertEqual(data["failed"]["status"]["analyst"], "failed")
        self.assertEqual(data["blocked_env"]["status"]["environment"], "failed")
        done = data["finished"]
        self.assertTrue(all(done["status"][role] == "done" for role in ("environment", "analyst", "planner", "coder", "tester", "reviewer")))
        self.assertEqual(done["status"]["result"], "done")
        self.assertEqual(done["status"]["limit"], "idle")
        self.assertEqual(data["interrupted"]["status"]["coder"], "stopped")


if __name__ == "__main__":
    unittest.main()
