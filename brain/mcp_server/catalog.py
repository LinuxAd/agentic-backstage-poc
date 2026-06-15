"""Backstage catalog client for the MCP server (stdlib only)."""
import json
import urllib.error
import urllib.request


def list_services(base_url):
    """Return [{entity_ref, name, owner, system}] for catalog Components."""
    url = base_url.rstrip("/") + "/api/catalog/entities?filter=kind=component"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            entities = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Backstage catalog returned {e.code} at {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Backstage unreachable at {url}: {e.reason}") from e

    services = []
    for ent in entities:
        md = ent.get("metadata", {})
        spec = ent.get("spec", {})
        ns = md.get("namespace", "default")
        name = md.get("name")
        services.append({
            "entity_ref": f"component:{ns}/{name}",
            "name": name,
            "owner": spec.get("owner"),
            "system": spec.get("system"),
        })
    return services
