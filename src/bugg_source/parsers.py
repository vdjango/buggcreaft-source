"""Pure interpretation of locally extracted metadata. No network, AI or source descriptions."""

import json
import tomllib
from typing import Any

from .contracts import CollectionError, Dependency, Issue, ModFact


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys rather than silently accepting different possible facts."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise CollectionError("duplicate_json_key")
        result[key] = value
    return result


def decode_document(name: str, raw: bytes) -> Any:
    text = raw.decode("utf-8-sig")
    if name.endswith(".toml"):
        return tomllib.loads(text)
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)


def reject_constant(value: str) -> None:
    raise CollectionError("invalid_json_constant")


def optional_string(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise CollectionError("invalid_string_field")
    return value


def required_id(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise CollectionError("invalid_mod_id")
    return value


def manifest_attributes(raw: bytes) -> dict[str, str]:
    """Read only main manifest section, including folded continuation lines."""
    text = raw.decode("utf-8").replace("\r\n", "\n")
    main = text.split("\n\n", 1)[0].replace("\n ", "")
    values = {}
    for line in main.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key.lower() in values:
                raise CollectionError("duplicate_manifest_attribute")
            values[key.lower()] = value
    return values


def manifest_version(raw: bytes) -> str | None:
    return manifest_attributes(raw).get("implementation-version")


def parse_fabric(document: Any, container: str, path: str) -> list[ModFact]:
    if not isinstance(document, dict):
        raise CollectionError("invalid_fabric_document")
    if document.get("schemaVersion") != 1:
        raise CollectionError("unsupported_fabric_schema")
    dependencies = []
    for relation in ("depends", "recommends", "suggests", "breaks", "conflicts"):
        values = document.get(relation, {})
        if not isinstance(values, dict):
            raise CollectionError("invalid_fabric_dependencies")
        for target_id, expression in values.items():
            if not (
                isinstance(expression, str)
                or isinstance(expression, list)
                and bool(expression)
                and all(isinstance(part, str) for part in expression)
            ):
                raise CollectionError("invalid_fabric_version_expression")
            dependencies.append(
                Dependency(
                    target_id=required_id(target_id),
                    relation=relation,
                    version_raw=expression,
                    environment=optional_string(document.get("environment")),
                    source_path=f"{path}#/{relation}/{target_id}",
                    raw=expression,
                )
            )
    provides = document.get("provides", [])
    if not isinstance(provides, list) or not all(isinstance(item, str) for item in provides):
        raise CollectionError("invalid_fabric_provides")
    version = optional_string(document.get("version"))
    if not version:
        raise CollectionError("missing_fabric_version")
    return [
        ModFact(
            mod_id=required_id(document.get("id")),
            declared_name=optional_string(document.get("name")),
            version_raw=version,
            version_resolved=version,
            loader="fabric",
            container_path=container,
            metadata_path=path,
            environment=optional_string(document.get("environment")),
            provides=provides,
            dependencies=dependencies,
            raw=document,
        )
    ]


def parse_toml(
    document: Any,
    container: str,
    path: str,
    implementation_version: str | None,
) -> tuple[list[ModFact], list[Issue]]:
    if not isinstance(document, dict) or not isinstance(document.get("mods"), list):
        raise CollectionError("invalid_mods_toml")
    declarations = document.get("dependencies", {})
    if not isinstance(declarations, dict):
        raise CollectionError("invalid_toml_dependencies")
    mods, issues = [], []
    known_ids = set()
    for ordinal, item in enumerate(document["mods"]):
        if not isinstance(item, dict):
            raise CollectionError("invalid_toml_mod")
        identifier = required_id(item.get("modId"))
        if identifier in known_ids:
            raise CollectionError("duplicate_mod_id")
        known_ids.add(identifier)
        dependencies = []
        rows = declarations.get(identifier, [])
        if not isinstance(rows, list):
            raise CollectionError("invalid_toml_dependencies")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise CollectionError("invalid_toml_dependency")
            if "type" in row:
                relation = required_id(row["type"]).lower()
                if relation not in ("required", "optional", "incompatible", "discouraged"):
                    raise CollectionError("unknown_neoforge_dependency_type")
            elif type(row.get("mandatory")) is bool:
                relation = "required" if row["mandatory"] else "optional"
            else:
                raise CollectionError("missing_dependency_requirement_type")
            dependencies.append(
                Dependency(
                    target_id=required_id(row.get("modId")),
                    relation=relation,
                    version_raw=optional_string(row.get("versionRange")),
                    environment=optional_string(row.get("side")),
                    ordering=optional_string(row.get("ordering")),
                    source_path=f"{path}#/dependencies/{identifier}/{index}",
                    raw=row,
                )
            )
        version = optional_string(item.get("version"))
        resolved = version
        if version == "${file.jarVersion}":
            resolved = implementation_version
        elif version and "${" in version:
            resolved = None
        if version and resolved is None:
            issues.append(
                Issue(
                    code="unresolved_version_placeholder",
                    path=f"{container}!/{path}",
                    detail=identifier,
                )
            )
        mods.append(
            ModFact(
                mod_id=identifier,
                declared_name=optional_string(item.get("displayName")),
                version_raw=version,
                version_resolved=resolved,
                loader="neoforge" if path.endswith("neoforge.mods.toml") else "forge_toml",
                container_path=container,
                metadata_path=f"{path}#/mods/{ordinal}",
                loader_constraints={
                    key: document[key] for key in ("modLoader", "loaderVersion") if key in document
                },
                dependencies=dependencies,
                raw=item,
            )
        )
    for identifier in declarations.keys() - known_ids:
        issues.append(
            Issue(code="orphan_dependency_owner", path=f"{container}!/{path}", detail=identifier)
        )
    return mods, issues


def nested_references(name: str, document: Any) -> list[str]:
    if not isinstance(document, dict):
        raise CollectionError("invalid_nested_jar_metadata")
    rows = document.get("jars", [])
    if not isinstance(rows, list):
        raise CollectionError("invalid_nested_jar_list")
    key = "file" if name == "fabric.mod.json" else "path"
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), str):
            raise CollectionError("invalid_nested_jar_reference")
        if not row[key].endswith(".jar"):
            raise CollectionError("invalid_nested_jar_reference")
        result.append(row[key])
    return list(dict.fromkeys(result))
