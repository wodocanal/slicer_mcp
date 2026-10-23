import json

from .errors import ToolError


class ToolRegistry(object):
    def __init__(self):
        self._tools = {}

    def add(self, name, description, properties, required, callback, read_only=True):
        self._tools[name] = {
            "schema": {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": bool(read_only),
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            },
            "callback": callback,
        }

    def schemas(self):
        return [self._tools[name]["schema"] for name in sorted(self._tools)]

    def call(self, name, arguments):
        if name not in self._tools:
            raise ToolError("Unknown tool: %s" % name)
        schema = self._tools[name]["schema"]["inputSchema"]
        for key in schema["required"]:
            if key not in arguments:
                raise ToolError("Missing required argument %s for %s" % (key, name))
        unknown = set(arguments) - set(schema["properties"])
        if unknown:
            raise ToolError("Unknown arguments for %s: %s" % (name, ", ".join(sorted(unknown))))
        return self._tools[name]["callback"](arguments)

    @staticmethod
    def to_json(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
