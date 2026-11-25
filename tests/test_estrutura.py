"""Testes do ticket F1-01 — esqueleto do projeto e CI.

Garantem a estrutura de pacotes, os arquivos de ferramental (pyproject, verificar.sh,
workflow de testes) e o `.env.exemplo` sem valores reais.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent

SUBPACOTES = (
    "fontes",
    "filtros",
    "classificacao",
    "enriquecimento",
    "notificacao",
    "banco",
    "config",
)

VARIAVEIS_DE_AMBIENTE = (
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "FUSO_HORARIO",
    "LIMITE_CHAMADAS_LLM_POR_EXECUCAO",
    "LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO",
)


# --- Pacote ---------------------------------------------------------------


def test_teste_trivial() -> None:
    """Teste trivial exigido pelo aceite do F1-01 (CI verde num PR com um teste trivial)."""
    assert 1 + 1 == 2


def test_pacote_radar_usa_layout_src() -> None:
    assert (RAIZ / "src" / "radar" / "__init__.py").is_file()


def test_pacote_radar_importavel_de_src() -> None:
    radar = importlib.import_module("radar")
    caminho = Path(radar.__file__).resolve()
    assert caminho == (RAIZ / "src" / "radar" / "__init__.py").resolve()


def test_radar_expoe_versao_como_string() -> None:
    radar = importlib.import_module("radar")
    assert isinstance(radar.__version__, str)
    assert radar.__version__.strip() != ""


@pytest.mark.parametrize("subpacote", SUBPACOTES)
def test_subpacote_existe_com_init(subpacote: str) -> None:
    assert (RAIZ / "src" / "radar" / subpacote / "__init__.py").is_file()


@pytest.mark.parametrize("subpacote", SUBPACOTES)
def test_subpacote_importavel(subpacote: str) -> None:
    modulo = importlib.import_module(f"radar.{subpacote}")
    assert modulo.__name__ == f"radar.{subpacote}"


# --- pyproject.toml ----------------------------------------------------------


def _ler_pyproject() -> dict:
    caminho = RAIZ / "pyproject.toml"
    assert caminho.is_file(), "pyproject.toml não existe na raiz"
    with caminho.open("rb") as arquivo:
        return tomllib.load(arquivo)


def _todas_dependencias(pyproject: dict) -> list[str]:
    projeto = pyproject.get("project", {})
    deps: list[str] = list(projeto.get("dependencies", []))
    for grupo in projeto.get("optional-dependencies", {}).values():
        deps.extend(grupo)
    for grupo in pyproject.get("dependency-groups", {}).values():
        deps.extend(d for d in grupo if isinstance(d, str))
    return deps


def _nome_dependencia(especificacao: str) -> str:
    nome = re.split(r"[<>=!~;\[\s]", especificacao, maxsplit=1)[0]
    return nome.strip().lower().replace("_", "-")


def test_pyproject_exige_python_312() -> None:
    projeto = _ler_pyproject()["project"]
    assert "3.12" in projeto.get("requires-python", "")


def test_pyproject_tem_dependencias_fixadas() -> None:
    deps = _todas_dependencias(_ler_pyproject())
    assert deps, "nenhuma dependência declarada"
    nao_fixadas = [d for d in deps if "==" not in d.split(";")[0]]
    assert nao_fixadas == [], f"dependências sem versão fixada (==): {nao_fixadas}"


@pytest.mark.parametrize("pacote", ["pydantic", "pydantic-settings", "pytest", "ruff", "pyyaml"])
def test_pyproject_declara_dependencia_essencial(pacote: str) -> None:
    nomes = {_nome_dependencia(d) for d in _todas_dependencias(_ler_pyproject())}
    assert pacote in nomes


@pytest.mark.parametrize("proibido", ["langchain", "langgraph", "redis"])
def test_pyproject_nao_usa_stack_proibida(proibido: str) -> None:
    nomes = {_nome_dependencia(d) for d in _todas_dependencias(_ler_pyproject())}
    assert not any(n.startswith(proibido) for n in nomes)


def test_pyproject_configura_ruff_lint_e_formatacao() -> None:
    ferramentas = _ler_pyproject().get("tool", {})
    assert "ruff" in ferramentas
    ruff = ferramentas["ruff"]
    assert "lint" in ruff, "seção [tool.ruff.lint] ausente"
    assert "format" in ruff, "seção [tool.ruff.format] ausente"


def test_pyproject_configura_pytest_com_testpaths() -> None:
    ferramentas = _ler_pyproject().get("tool", {})
    opcoes = ferramentas.get("pytest", {}).get("ini_options", {})
    assert "tests" in opcoes.get("testpaths", [])


# --- scripts/verificar.sh ------------------------------------------------------


def _ler_verificar() -> str:
    caminho = RAIZ / "scripts" / "verificar.sh"
    assert caminho.is_file(), "scripts/verificar.sh não existe"
    return caminho.read_text(encoding="utf-8")


def test_verificar_sh_e_executavel() -> None:
    caminho = RAIZ / "scripts" / "verificar.sh"
    assert caminho.is_file()
    assert os.access(caminho, os.X_OK), "scripts/verificar.sh sem permissão de execução"


def test_verificar_sh_tem_shebang_e_falha_no_primeiro_erro() -> None:
    conteudo = _ler_verificar()
    assert conteudo.startswith("#!"), "falta shebang"
    assert re.search(r"set\s+-[a-z]*e", conteudo), "falta `set -e` (ou -eu/-euo pipefail)"


def test_verificar_sh_roda_ruff_lint_formatacao_e_pytest() -> None:
    conteudo = _ler_verificar()
    assert re.search(r"ruff\s+check", conteudo), "falta `ruff check`"
    assert re.search(r"ruff\s+format\s+--check", conteudo), "falta `ruff format --check`"
    assert "pytest" in conteudo, "falta pytest"


# --- Workflow de CI ------------------------------------------------------------


def _ler_texto_workflow() -> str:
    caminho = RAIZ / ".github" / "workflows" / "testes.yml"
    assert caminho.is_file(), ".github/workflows/testes.yml não existe"
    return caminho.read_text(encoding="utf-8")


def _ler_workflow() -> dict:
    return yaml.safe_load(_ler_texto_workflow())


def test_workflow_testes_roda_em_push_e_pull_request() -> None:
    workflow = _ler_workflow()
    # PyYAML interpreta a chave `on` como o booleano True.
    gatilhos = workflow.get("on", workflow.get(True))
    if isinstance(gatilhos, str):
        gatilhos = [gatilhos]
    assert "push" in gatilhos
    assert "pull_request" in gatilhos


def test_workflow_testes_executa_verificar_sh() -> None:
    workflow = _ler_workflow()
    comandos = [
        passo.get("run", "") for job in workflow["jobs"].values() for passo in job.get("steps", [])
    ]
    assert any("scripts/verificar.sh" in c for c in comandos)


def test_workflow_testes_usa_python_312() -> None:
    texto = _ler_texto_workflow()
    assert re.search(r"python-version:\s*['\"]?3\.12", texto)


def test_workflow_testes_nao_contem_segredos_literais() -> None:
    texto = _ler_texto_workflow()
    assert not re.search(r"sk-ant-[A-Za-z0-9]", texto)
    assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", texto)  # formato de token do Telegram


# --- .env.exemplo --------------------------------------------------------------


def _ler_env_exemplo() -> dict[str, str]:
    caminho = RAIZ / ".env.exemplo"
    assert caminho.is_file(), ".env.exemplo não existe na raiz"
    variaveis: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        nome, sep, valor = linha.partition("=")
        assert sep == "=", f"linha inválida em .env.exemplo: {linha!r}"
        variaveis[nome.strip()] = valor.strip()
    return variaveis


@pytest.mark.parametrize("variavel", VARIAVEIS_DE_AMBIENTE)
def test_env_exemplo_lista_variavel(variavel: str) -> None:
    assert variavel in _ler_env_exemplo()


@pytest.mark.parametrize("segredo", ["DATABASE_URL", "ANTHROPIC_API_KEY", "TELEGRAM_TOKEN"])
def test_env_exemplo_nao_tem_valor_real_de_segredo(segredo: str) -> None:
    valor = _ler_env_exemplo()[segredo].strip("'\"")
    assert valor == "", f"{segredo} deve estar vazio em .env.exemplo"


def test_env_exemplo_nao_tem_chat_id_real() -> None:
    valor = _ler_env_exemplo()["TELEGRAM_CHAT_ID"].strip("'\"")
    assert valor == "", "TELEGRAM_CHAT_ID é dado pessoal e deve estar vazio"


def test_env_exemplo_nao_e_ignorado_pelo_git() -> None:
    """O `.gitignore` atual tem `.env.*`, que esconderia o `.env.exemplo`."""
    if shutil.which("git") is None or not (RAIZ / ".git").exists():
        pytest.skip("git indisponível")
    resultado = subprocess.run(
        ["git", "check-ignore", "-q", ".env.exemplo"],
        cwd=RAIZ,
        check=False,
    )
    assert resultado.returncode == 1, ".env.exemplo está sendo ignorado pelo git"


def test_env_real_continua_ignorado_pelo_git() -> None:
    if shutil.which("git") is None or not (RAIZ / ".git").exists():
        pytest.skip("git indisponível")
    resultado = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=RAIZ,
        check=False,
    )
    assert resultado.returncode == 0, ".env precisa continuar no .gitignore"


def test_env_exemplo_bate_com_campos_da_configuracao() -> None:
    from radar.config import Configuracao

    campos = {nome.upper() for nome in Configuracao.model_fields}
    assert campos == set(_ler_env_exemplo())
