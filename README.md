# Astermesh

Deterministic Code Knowledge Graph & Local Context Layer (MCP Server) cho AI Coding Assistants.

## 1. Cai dat

Yeu cau: Python >= 3.10

```bash
# Khoi tao moi truong ao
python3 -m venv .venv
source .venv/bin/activate

# Cai dat dependencies o che do editable
pip install -e .
```

## 2. Cach su dung

### Che do CLI Snapshot (Chay thu cong)

Quet repository va tu dong xuat vao thu muc `xml-graph/` gom Semantic XML (cho AI) va ban do tuong tac 2D HTML (cho con nguoi):

```bash
# Xuat ca XML va HTML vao thu muc xml-graph/ mac dinh
astermesh build .

# Chi xuat XML, bo qua HTML
astermesh build . --no-html

# Xuat vao mot thu muc khac tuy chon
astermesh build . -o custom-output-dir
```

Ket qua dau ra trong thu muc `xml-graph/`:
- `xml-graph/context_graph.xml`: Ngu canh topo gom cum danh cho co che Attention cua LLM.
- `xml-graph/astermesh_view.html`: Ban do mang luoi 2D danh cho con nguoi theo doi truc quan.

Tra cuu toa do thuc the ma nguon tu dong lenh:

```bash
astermesh query "GraphBuilder"
```

### Che do MCP Daemon Server (Pair Programming voi AI)

Khoi chay server MCP tich hop File Watcher tu dong cap nhat vi sai:

```bash
# Chay qua stdio (danh cho Cursor, Claude Desktop, Antigravity)
astermesh serve . --transport stdio

# Chay qua SSE HTTP
astermesh serve . --transport sse --host 127.0.0.1 --port 8000
```

### Cau hinh MCP Client (Cursor / Claude Desktop)

Them vao file cau hinh MCP:

```json
{
  "mcpServers": {
    "astermesh": {
      "command": "astermesh",
      "args": ["serve", "/duong/dan/toi/du_an", "--transport", "stdio"]
    }
  }
}
```

## 3. Cac cong cu MCP cung cap

- `locate_symbol`: Tim kiem dinh danh, tra ve duong dan tep va pham vi dong ma xac dinh `[line_start, line_end]`.
- `trace_path`: Tim duong di tuong tac giua 2 thuc the logic.
- `get_community`: Lay toan bo thuc the thuoc mot phan he chuc nang dua tren thuat toan Leiden.
- `rebuild_graph`: Buoc quet lai toan bo do thi tren RAM khi can thiet.
