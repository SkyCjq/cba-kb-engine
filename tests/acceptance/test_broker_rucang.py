"""Broker 入仓验收测试（PR #127，T2）。

验证 3 个入仓文件：存在性、SHA256 与 R8 PASS_WITH_NOTES 审核版一致、
workflow YAML 可解析、Python 脚本可编译。

R8 审核：Web R1–R8，R8 PASS_WITH_NOTES（2026-10-10），FND-30/31/32 已修。
入仓包：~/workspace/mode2-bot-broker/HUMAN_REVIEW_ENTRY_PACK_2026-10-10.md
"""
import hashlib
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# R8 PASS_WITH_NOTES 审核版的完整 SHA256（64 位）
EXPECTED_SHA256 = {
    ".github/workflows/bot-pr-broker.yml":
        "e2c1ec1dcac62f3203bf60efba6a57928d60bec44254800d498a17486d30c7e3",
    ".github/scripts/bot-broker-validate.py":
        "5788d9589c6eb69134e4aac4023c44c3a635bbc70ce33f4c8d3be9a85a63863a",
    ".github/scripts/bot-broker-receipt.py":
        "21c3a7ea3113dc79b6c97933487d71d334e0e886fe97a235a9dd8e530b8cd219",
}


def _sha256(rel: str) -> str:
    return hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()


def test_rucang_files_exist():
    """3 个入仓文件存在于预期路径。"""
    for rel in EXPECTED_SHA256:
        assert (REPO_ROOT / rel).is_file(), f"缺失入仓文件: {rel}"


def test_rucang_sha256_match():
    """文件内容与 R8 审核版逐字节一致（防篡改）。"""
    for rel, exp in EXPECTED_SHA256.items():
        got = _sha256(rel)
        assert got == exp, f"{rel} SHA 不匹配: {got[:12]} vs {exp[:12]}"


def test_workflow_yaml_parses():
    """workflow YAML 可解析且关键字段正确。"""
    import yaml

    text = (REPO_ROOT / ".github/workflows/bot-pr-broker.yml").read_text(
        encoding="utf-8"
    )
    doc = yaml.safe_load(text)
    assert doc["name"] == "bot-pr-broker"
    assert "broker" in doc["jobs"]
    triggers = doc.get("on", {})
    assert "repository_dispatch" in triggers


def test_scripts_compile():
    """两个 Python 脚本可编译（无语法错误）。"""
    import py_compile

    for rel in EXPECTED_SHA256:
        if rel.endswith(".py"):
            py_compile.compile(str(REPO_ROOT / rel), doraise=True)
