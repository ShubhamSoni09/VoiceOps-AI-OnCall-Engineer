from __future__ import annotations

import re
from collections import Counter

from app.collab.models import AgentAction, MemoryItem, RoomSnapshot, TimelineMessage
from app.collab.service import CollaborationService
from app.ontology.models import OntologyEdge, OntologyGraph, OntologyNode, OntologyQueryResponse

FILE_RE = re.compile(r"\b[\w./-]+\.(?:py|js|jsx|ts|tsx|md|json|ya?ml|css|html|sql|sh)\b", re.IGNORECASE)


class OntologyService:
    def __init__(self, collab: CollaborationService) -> None:
        self._collab = collab

    def build_graph(self, room_id: str) -> OntologyGraph:
        snapshot = self._collab.snapshot(room_id)
        nodes: dict[str, OntologyNode] = {}
        edges: dict[str, OntologyEdge] = {}

        for participant in snapshot.participants:
            _add_node(
                nodes,
                f"person:{participant.id}",
                "person",
                participant.name,
                {"initials": participant.initials, "kind": participant.kind.value, "online": participant.online},
            )

        for message in snapshot.messages:
            _index_message(nodes, edges, message)
        for item in snapshot.memory:
            _index_memory(nodes, edges, item)
        for action in snapshot.actions:
            _index_action(nodes, edges, action)

        summary = Counter(node.kind for node in nodes.values())
        summary.update(f"edge:{edge.relation}" for edge in edges.values())
        return OntologyGraph(
            room_id=room_id,
            nodes=sorted(nodes.values(), key=lambda node: (node.kind, node.label, node.id)),
            edges=sorted(edges.values(), key=lambda edge: (edge.relation, edge.source, edge.target)),
            summary=dict(summary),
        )

    def query(self, room_id: str, question: str, *, limit: int = 12) -> OntologyQueryResponse:
        graph = self.build_graph(room_id)
        if _asks_for_files(question):
            selected_nodes = [node for node in graph.nodes if node.kind == "file"][:limit]
            selected_ids = {node.id for node in selected_nodes}
            selected_edges = [
                edge
                for edge in graph.edges
                if edge.source in selected_ids or edge.target in selected_ids
            ][: limit * 2]
            return OntologyQueryResponse(
                answer=_answer(question, selected_nodes, selected_edges),
                nodes=selected_nodes,
                edges=selected_edges,
            )
        terms = [term for term in re.findall(r"[a-zA-Z0-9_.-]+", question.lower()) if len(term) > 1]
        ranked_nodes = [
            (node, _score_node(node, terms))
            for node in graph.nodes
        ]
        selected_nodes = [node for node, score in sorted(ranked_nodes, key=lambda item: item[1], reverse=True) if score > 0]
        if not selected_nodes and _asks_for_people(question):
            selected_nodes = [node for node in graph.nodes if node.kind == "person"]
        selected_nodes = selected_nodes[:limit]
        selected_ids = {node.id for node in selected_nodes}
        selected_edges = [
            edge
            for edge in graph.edges
            if edge.source in selected_ids or edge.target in selected_ids
        ][: limit * 2]
        answer = _answer(question, selected_nodes, selected_edges)
        return OntologyQueryResponse(answer=answer, nodes=selected_nodes, edges=selected_edges)


def _index_message(nodes: dict[str, OntologyNode], edges: dict[str, OntologyEdge], message: TimelineMessage) -> None:
    person_id = f"person:{message.actor_id}"
    _add_node(nodes, person_id, "person", message.actor_name, {"initials": message.actor_initials})
    for file_path in _files_from_text(message.text):
        file_id = _file_id(file_path)
        _add_node(nodes, file_id, "file", file_path)
        _add_edge(edges, person_id, file_id, "mentioned_file", {"source": "message", "message_id": message.id})


def _index_memory(nodes: dict[str, OntologyNode], edges: dict[str, OntologyEdge], item: MemoryItem) -> None:
    memory_id = f"memory:{item.id}"
    person_id = f"person:{item.actor_id}"
    _add_node(nodes, person_id, "person", item.actor_name)
    _add_node(nodes, memory_id, f"memory:{item.kind.value}", item.text, {"status": item.status})
    _add_edge(edges, person_id, memory_id, "recorded_memory", {"kind": item.kind.value})
    for file_path in _files_from_text(item.text, *[str(value) for value in item.metadata.values()]):
        file_id = _file_id(file_path)
        _add_node(nodes, file_id, "file", file_path)
        _add_edge(edges, memory_id, file_id, "mentions_file", {"kind": item.kind.value})


def _index_action(nodes: dict[str, OntologyNode], edges: dict[str, OntologyEdge], action: AgentAction) -> None:
    action_id = f"action:{action.id}"
    requester_id = f"person:{action.requested_by}"
    _add_node(nodes, requester_id, "person", action.requested_by_name)
    _add_node(
        nodes,
        action_id,
        "action",
        action.summary,
        {"status": action.status, "action": str(action.action), "pending_approval": action.pending_approval},
    )
    _add_edge(edges, requester_id, action_id, "requested_action")
    approval = action.approval or {}
    approver = approval.get("decided_by")
    approver_name = approval.get("decided_by_name")
    if approver and approver_name:
        approver_id = f"person:{approver}"
        _add_node(nodes, approver_id, "person", str(approver_name))
        _add_edge(edges, approver_id, action_id, "approved_action", {"status": approval.get("status")})
    git = approval.get("git") if isinstance(approval.get("git"), dict) else {}
    branch = git.get("branch_name")
    if branch:
        branch_id = f"branch:{branch}"
        _add_node(nodes, branch_id, "branch", branch)
        _add_edge(edges, action_id, branch_id, "created_branch")
    for file_path in [*action.files_changed, *_files_from_text(action.summary, str(action.approval))]:
        file_id = _file_id(file_path)
        _add_node(nodes, file_id, "file", file_path)
        _add_edge(edges, action_id, file_id, "touches_file", {"status": action.status})


def _add_node(
    nodes: dict[str, OntologyNode],
    node_id: str,
    kind: str,
    label: str,
    metadata: dict | None = None,
) -> None:
    if node_id not in nodes:
        nodes[node_id] = OntologyNode(id=node_id, kind=kind, label=label, metadata=metadata or {})


def _add_edge(
    edges: dict[str, OntologyEdge],
    source: str,
    target: str,
    relation: str,
    metadata: dict | None = None,
) -> None:
    edge_id = f"{source}->{relation}->{target}"
    if edge_id not in edges:
        edges[edge_id] = OntologyEdge(id=edge_id, source=source, target=target, relation=relation, metadata=metadata or {})


def _files_from_text(*values: str) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        for match in FILE_RE.findall(value or ""):
            seen[match.strip(".,;:")] = None
    return list(seen)


def _file_id(path: str) -> str:
    return f"file:{path}"


def _score_node(node: OntologyNode, terms: list[str]) -> int:
    haystack = " ".join([node.kind, node.label, " ".join(str(value) for value in node.metadata.values())]).lower()
    return sum(1 for term in terms if term in haystack)


def _asks_for_files(question: str) -> bool:
    lower = question.lower()
    return "file" in lower or "files" in lower or "文件" in question


def _asks_for_people(question: str) -> bool:
    lower = question.lower()
    return "who" in lower or "alice" in lower or "bob" in lower or "谁" in question


def _answer(question: str, nodes: list[OntologyNode], edges: list[OntologyEdge]) -> str:
    if not nodes:
        return "No ontology nodes matched that question yet."
    if _asks_for_files(question):
        files = [node.label for node in nodes if node.kind == "file"]
        if files:
            return "Files in project context: " + ", ".join(files[:8]) + "."
    labels = ", ".join(f"{node.kind} {node.label}" for node in nodes[:5])
    return f"Ontology context: {labels}. Relations: {len(edges)}."
