from .assets import assets_route
from .projects import projects_route
from .projectsMETA import projectsMETA_route

def register_routes(app):
    app.add_url_rule("/assets", view_func=assets_route)
    app.add_url_rule("/projects", view_func=projects_route)
    app.add_url_rule("/projectsMETA", view_func=projectsMETA_route)
