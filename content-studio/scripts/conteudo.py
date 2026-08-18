"""Utilitários para listar e gerenciar conteúdo salvo."""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "conteudo"
CALENDARIO = BASE / "calendario.json"


def carregar_calendario() -> dict:
    return json.loads(CALENDARIO.read_text(encoding="utf-8"))


def carregar_post(post_id: str) -> dict:
    cal = carregar_calendario()
    for item in cal["posts"]:
        if item["id"] == post_id:
            path = BASE / item["arquivo"]
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Post {post_id} não encontrado")


def listar_posts(status: str | None = None) -> list[dict]:
    cal = carregar_calendario()
    posts = []
    for item in cal["posts"]:
        if status and item.get("status") != status:
            continue
        path = BASE / item["arquivo"]
        post = json.loads(path.read_text(encoding="utf-8"))
        post["_calendario"] = item
        posts.append(post)
    return posts


def resumo() -> None:
    cal = carregar_calendario()
    print(f"Projeto: {cal['projeto']}")
    print(f"Tema: {cal['tema']}")
    print(f"Posts: {len(cal['posts'])}")
    print()
    for item in cal["posts"]:
        post = json.loads((BASE / item["arquivo"]).read_text(encoding="utf-8"))
        manim = item.get("manim") or "—"
        print(f"  [{item['id']}] {post['titulo'][:50]:50} | {item['status']:10} | manim: {manim}")


if __name__ == "__main__":
    resumo()
