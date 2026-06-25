"""
data_exporter — 数据导出插件
═══════════════════════════════
将 Pipeline 产物导出为 JSON 或 Markdown 格式。
"""

import json
from datetime import datetime
from pathlib import Path


def on_load(config=None, engine=None):
    if engine:
        engine.log("data_exporter loaded")
    return config


async def export(data: dict, config=None, **kwargs) -> dict:
    if config is None:
        config = {}
    export_format = config.get("export_format", "json")
    export_dir = Path(config.get("export_dir", "workspace/exports"))
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {"format": export_format, "files": []}
    if export_format == "json":
        result.update(_export_json(data, config, export_dir, timestamp))
    elif export_format == "markdown":
        result.update(_export_markdown(data, config, export_dir, timestamp))
    return result


async def on_hook(context=None, config=None, hook_point="", **kwargs):
    if not context or config is None:
        return None
    if not config.get("auto_export_on_complete", True):
        return None
    data = {
        "requirements": context.data.get("requirements", ""),
        "python_code": context.data.get("python_code", ""),
        "html_code": context.data.get("html_code", ""),
        "structure": context.data.get("structure", {}),
        "review_findings": context.data.get("review_findings", []),
        "exported_at": datetime.now().isoformat(),
    }
    result = await export(data=data, config=config)
    if result.get("files"):
        context.set_data("export_files", result["files"])
    return {"data": {"export_result": result}}


def _export_json(data: dict, config: dict, export_dir: Path, timestamp: str) -> dict:
    files = []
    export_data = {}
    if config.get("include_requirements", True):
        export_data["requirements"] = data.get("requirements", "")
    if config.get("include_code", True):
        if data.get("python_code"):
            export_data["python_code"] = data["python_code"]
        if data.get("html_code"):
            export_data["html_code"] = data["html_code"]
    export_data["structure"] = data.get("structure", {})
    export_data["exported_at"] = data.get("exported_at", "")
    fp = export_dir / f"project_{timestamp}.json"
    fp.write_text(json.dumps(export_data, indent=2, ensure_ascii=False), encoding="utf-8")
    files.append(str(fp))
    if config.get("include_review", True) and data.get("review_findings"):
        fp2 = export_dir / f"review_{timestamp}.json"
        fp2.write_text(json.dumps(data["review_findings"], indent=2, ensure_ascii=False), encoding="utf-8")
        files.append(str(fp2))
    return {"files": files}


def _export_markdown(data: dict, config: dict, export_dir: Path, timestamp: str) -> dict:
    sections = [f"# 项目导出报告\n> 导出时间: {data.get('exported_at', '')}\n"]
    if config.get("include_requirements", True) and data.get("requirements"):
        sections.append(f"\n## 需求文档\n{data['requirements']}")
    if data.get("structure"):
        sections.append(f"\n## 项目结构\n```json\n{json.dumps(data['structure'], indent=2, ensure_ascii=False)}\n```")
    if config.get("include_code", True) and data.get("python_code"):
        sections.append(f"\n## Python 代码\n```python\n{data['python_code']}\n```")
    if config.get("include_code", True) and data.get("html_code"):
        sections.append(f"\n## HTML 代码\n```html\n{data['html_code']}\n```")
    if config.get("include_review", True) and data.get("review_findings"):
        sections.append(f"\n## 审查报告\n共 {len(data['review_findings'])} 个问题")
    fp = export_dir / f"project_{timestamp}.md"
    fp.write_text("\n".join(sections), encoding="utf-8")
    return {"files": [str(fp)]}
