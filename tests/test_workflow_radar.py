"""Testes do F1-10d — workflow agendado `.github/workflows/radar.yml` e seção de reativação.

Regras: nome "Radar"; gatilhos só `schedule` (um cron "40 10,15,22 * * *", 7h40, 12h40 e 19h40
em SP) e `workflow_dispatch` sem inputs; `permissions: contents: read`; concorrência
`radar-executar` sem cancelar; um job em ubuntu-latest com timeout ≤ 20 min; passos checkout@v4,
setup-python@v5 (3.12), `pip install -e .`, "Migrar" (`radar migrar`, env só DATABASE_URL) e
"Executar" (`radar executar --ultimo-commit-em "$(git log -1 --format=%cI)"`, env com os 4
secrets). Secrets só no `env` desses passos; nada de set -x/env/printenv, upload-artifact ou
git push.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOW = RAIZ / ".github" / "workflows" / "radar.yml"
SECRETS = ("DATABASE_URL", "ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
_SECRET = re.compile(r"secrets\.([A-Z_]+)")


@pytest.fixture
def texto() -> str:
    assert WORKFLOW.is_file(), "falta .github/workflows/radar.yml"
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture
def workflow(texto: str) -> dict[Any, Any]:
    dados = yaml.safe_load(texto)
    assert isinstance(dados, dict)
    return dados


def _gatilhos(workflow: dict[Any, Any]) -> dict[str, Any]:
    gatilhos = workflow.get("on", workflow.get(True))  # PyYAML lê `on:` como True
    assert isinstance(gatilhos, dict)
    return gatilhos


def _job(workflow: dict[Any, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))


def _passos(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return list(_job(workflow)["steps"])


def _passo(workflow: dict[Any, Any], nome: str) -> dict[str, Any]:
    achados = [p for p in _passos(workflow) if p.get("name") == nome]
    assert len(achados) == 1, f"passo {nome!r}"
    return achados[0]


# --- Cabeçalho -----------------------------------------------------------------------------


def test_nome(workflow: dict[Any, Any]) -> None:
    assert workflow["name"] == "Radar"


def test_gatilhos(workflow: dict[Any, Any]) -> None:
    gatilhos = _gatilhos(workflow)
    assert set(gatilhos) == {"schedule", "workflow_dispatch"}
    assert gatilhos["schedule"] == [{"cron": "40 10,15,22 * * *"}]
    manual = gatilhos["workflow_dispatch"]
    assert not manual or not manual.get("inputs")


def test_permissoes_so_leitura(workflow: dict[Any, Any], texto: str) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in _job(workflow) or _job(workflow)["permissions"] == {
        "contents": "read"
    }
    assert not re.search(r":\s*write\b|write-all", texto)


def test_concorrencia(workflow: dict[Any, Any]) -> None:
    concorrencia = workflow.get("concurrency") or _job(workflow).get("concurrency")
    assert concorrencia == {"group": "radar-executar", "cancel-in-progress": False}


def test_job(workflow: dict[Any, Any]) -> None:
    job = _job(workflow)
    assert job["runs-on"] == "ubuntu-latest"
    assert 0 < int(job["timeout-minutes"]) <= 20


# --- Passos --------------------------------------------------------------------------------


def test_checkout_e_python(workflow: dict[Any, Any]) -> None:
    passos = _passos(workflow)
    usos = [p.get("uses") for p in passos]
    assert "actions/checkout@v4" in usos
    [python] = [p for p in passos if p.get("uses") == "actions/setup-python@v5"]
    assert str(python["with"]["python-version"]) == "3.12"


def test_instalacao(workflow: dict[Any, Any]) -> None:
    runs = [str(p.get("run", "")) for p in _passos(workflow)]
    assert any("pip install -e ." in r for r in runs)


def test_ordem_dos_passos(workflow: dict[Any, Any]) -> None:
    passos = _passos(workflow)
    indice = {id(p): i for i, p in enumerate(passos)}
    checkout = next(p for p in passos if p.get("uses") == "actions/checkout@v4")
    instalar = next(p for p in passos if "pip install -e ." in str(p.get("run", "")))
    migrar = _passo(workflow, "Migrar")
    executar = _passo(workflow, "Executar")
    assert indice[id(checkout)] < indice[id(instalar)] < indice[id(migrar)] < indice[id(executar)]


def test_migrar(workflow: dict[Any, Any]) -> None:
    passo = _passo(workflow, "Migrar")
    assert passo["run"].strip() == "radar migrar"
    assert passo["env"] == {"DATABASE_URL": "${{ secrets.DATABASE_URL }}"}


def test_executar(workflow: dict[Any, Any]) -> None:
    passo = _passo(workflow, "Executar")
    assert passo["run"].strip() == 'radar executar --ultimo-commit-em "$(git log -1 --format=%cI)"'
    assert passo["env"] == {nome: f"${{{{ secrets.{nome} }}}}" for nome in SECRETS}


# --- Secrets e higiene ---------------------------------------------------------------------


def test_secrets_so_no_env_dos_passos(workflow: dict[Any, Any], texto: str) -> None:
    assert not _SECRET.search(str(workflow.get("env", "")))
    assert not _SECRET.search(str(_job(workflow).get("env", "")))
    for passo in _passos(workflow):
        assert not _SECRET.search(str(passo.get("run", "")))
        assert not _SECRET.search(str(passo.get("with", "")))
        nomes = _SECRET.findall(str(passo.get("env", "")))
        assert len(nomes) == len(set(nomes)), passo.get("name")
        if nomes:
            assert passo.get("name") in {"Migrar", "Executar"}
    assert _SECRET.findall(texto).count("DATABASE_URL") == 2
    for nome in SECRETS[1:]:
        assert _SECRET.findall(texto).count(nome) == 1


def test_migrar_sem_secrets_do_llm_e_telegram(workflow: dict[Any, Any]) -> None:
    env = str(_passo(workflow, "Migrar").get("env", ""))
    for nome in ("ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        assert nome not in env


def test_runs_sem_vazamento(workflow: dict[Any, Any]) -> None:
    for passo in _passos(workflow):
        run = str(passo.get("run", ""))
        assert "set -x" not in run
        assert not re.search(r"(^|[;&|\s])(env|printenv)(\s|$)", run), passo.get("name")
        assert "git push" not in run


def test_sem_artefatos(workflow: dict[Any, Any]) -> None:
    for passo in _passos(workflow):
        assert "upload-artifact" not in str(passo.get("uses", ""))


# --- README --------------------------------------------------------------------------------


def _secao_reativacao() -> str:
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    partes = re.split(r"^## ", texto, flags=re.MULTILINE)
    achadas = [p for p in partes if p.startswith("Reativação")]
    assert len(achadas) == 1, 'README.md precisa de uma seção "## Reativação"'
    return achadas[0]


def test_readme_tem_reativacao() -> None:
    secao = _secao_reativacao()
    assert "60 dias" in secao
    assert "Actions" in secao
    assert "50 dias" in secao
    assert "Enable workflow" in secao
