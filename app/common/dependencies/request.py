from fastapi import Request


def get_database(request: Request):
    return request.app.state.database


def get_redis(request: Request):
    return request.app.state.redis


def get_settings(request: Request):
    return request.app.state.settings
