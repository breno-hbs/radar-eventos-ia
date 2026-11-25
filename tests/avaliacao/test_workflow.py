"""Testes do F1-07 — pacote `radar.avaliacao` e workflow manual `avaliar.yml`.

Regras do workflow: só `workflow_dispatch` (input `conjunto`, choice teste|dev), nunca em
pull_request; `permissions: contents: write`; o secret ANTHROPIC_API_KEY aparece uma única
vez, no `env` do passo que roda `python -m radar.avaliacao` (nunca em `run`, nem no env do
job/workflow); grava `docs/avaliacao/<conjunto>-classificador_v2.md` (sufixo fixo, igual
ao VERSAO_PROMPT atual) e publica num branch novo
`fase-1/f1-07-avaliacao-${{ github.run_number }}`, sem push na main e sem forçar.

F1-07b: o passo da avaliação usa `--diagnostico diagnostico-avaliacao`; um passo
`actions/upload-artifact@v4` com `if: always()`, path diagnostico-avaliacao,
retention-days 7 e if-no-files-found: ignore publica o diagnóstico, sem o secret.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
WORKFLOW = RAIZ / ".github" / "workflows" / "avaliar.yml"
SECRET = "secrets.ANTHROPIC_API_KEY"


def test_pacote_avaliacao_existe() -> None:
    assert (RAIZ / "src" / "radar" / "avaliacao" / "__init__.py").is_file()
    assert (RAIZ / "src" / "radar" / "avaliacao" / "__main__.py").is_file()


@pytest.fixture
def texto() -> str:
    assert WORKFLOW.is_file(), "falta .github/workflows/avaliar.yml"
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture
def workflow(texto: str) -> dict[Any, Any]:
    dados = yaml.safe_load(texto)
    assert isinstance(dados, dict)
    return dados


def _gatilhos(workflow: dict[Any, Any]) -> dict[str, Any]:
    gatilhos = workflow.get("on", workflow.get(True))  # PyYAML lê `on:` como True
    if isinstance(gatilhos, str):
        return {gatilhos: None}
    if isinstance(gatilhos, list):
        return dict.fromkeys(gatilhos)
    assert isinstance(gatilhos, dict)
    return gatilhos


def _passos(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return [passo for job in workflow["jobs"].values() for passo in job.get("steps", [])]


def test_so_disparo_manual(workflow: dict[Any, Any]) -> None:
    gatilhos = _gatilhos(workflow)
    assert set(gatilhos) == {"workflow_dispatch"}
    assert "pull_request" not in gatilhos
    assert "pull_request_target" not in gatilhos


def test_input_conjunto(workflow: dict[Any, Any]) -> None:
    entrada = _gatilhos(workflow)["workflow_dispatch"]["inputs"]["conjunto"]
    assert entrada["type"] == "choice"
    assert set(entrada["options"]) == {"teste", "dev"}


def test_permissao_de_escrita(workflow: dict[Any, Any]) -> None:
    assert workflow["permissions"] == {"contents": "write"}


def test_secret_so_no_env_do_passo_de_avaliacao(workflow: dict[Any, Any], texto: str) -> None:
    assert texto.count(SECRET) == 1
    assert SECRET not in str(workflow.get("env", ""))
    for job in workflow["jobs"].values():
        assert SECRET not in str(job.get("env", ""))
    passos = _passos(workflow)
    com_secret = [p for p in passos if SECRET in str(p.get("env", ""))]
    assert len(com_secret) == 1
    [passo] = com_secret
    assert re.fullmatch(
        r"\$\{\{\s*secrets\.ANTHROPIC_API_KEY\s*\}\}", passo["env"]["ANTHROPIC_API_KEY"]
    )
    assert "python -m radar.avaliacao" in passo["run"]
    assert "--conjunto" in passo["run"]
    for outro in passos:
        assert SECRET not in str(outro.get("run", ""))
        assert SECRET not in str(outro.get("with", ""))


def test_grava_relatorio_e_publica_em_branch_novo(workflow: dict[Any, Any], texto: str) -> None:
    assert "docs/avaliacao/" in texto
    prompt = importlib.import_module("radar.classificacao.prompt")
    versao = prompt.VERSAO_PROMPT
    assert versao == "classificador_v2"
    assert f"-{versao}.md" in texto
    assert "classificador_v1" not in texto
    passo = _passo_avaliacao(workflow)
    assert f"-{versao}.md" in passo["run"]
    runs = "\n".join(str(p.get("run", "")) for p in _passos(workflow))
    assert "fase-1/f1-07-avaliacao-${{ github.run_number }}" in runs
    commits = [linha for linha in runs.splitlines() if "git commit" in linha]
    assert commits
    assert all(versao in linha for linha in commits)
    assert "git push" in runs
    assert "--force" not in runs
    assert not re.search(r"git push\s+(-f|origin\s+main\b|origin\s+HEAD:main)", runs)


# --- F1-07b: diagnóstico como artefato ------------------------------------------------------

DIR_DIAGNOSTICO = "diagnostico-avaliacao"


def _passo_avaliacao(workflow: dict[Any, Any]) -> dict[str, Any]:
    [passo] = [p for p in _passos(workflow) if "python -m radar.avaliacao" in str(p.get("run"))]
    return passo


def test_avaliacao_grava_diagnostico(workflow: dict[Any, Any]) -> None:
    run = _passo_avaliacao(workflow)["run"]
    assert re.search(rf"--diagnostico[ =]\"?{DIR_DIAGNOSTICO}\b", run)


def test_diagnostico_publicado_como_artefato(workflow: dict[Any, Any]) -> None:
    passos = _passos(workflow)
    uploads = [p for p in passos if str(p.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(uploads) == 1
    [upload] = uploads
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert "always()" in str(upload.get("if", ""))
    opcoes = upload["with"]
    assert str(opcoes["path"]).strip() == DIR_DIAGNOSTICO
    assert int(opcoes["retention-days"]) == 7
    assert opcoes["if-no-files-found"] == "ignore"
    assert SECRET not in str(upload)
    assert passos.index(upload) > passos.index(_passo_avaliacao(workflow))
