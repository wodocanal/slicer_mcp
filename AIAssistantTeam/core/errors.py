class AssistantError(Exception):
    pass


class ConfigurationError(AssistantError):
    pass


class ToolError(AssistantError):
    pass
