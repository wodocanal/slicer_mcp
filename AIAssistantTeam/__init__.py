def getMetaData():
    return {}


def register(app):
    from . import AIAssistant

    return {"extension": AIAssistant.AIAssistant()}
