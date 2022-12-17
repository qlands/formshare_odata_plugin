import formshare.plugins as plugins
import formshare.plugins.utilities as u
from .views import (
    ODataGenerateView,
    ODataCheckView,
    ODataUsersView,
    ODataTableAccessView,
    ODataChangeAccessView,
    ODataActionView,
)
import sys
import os
from formshare.models import (
    Odkform,
    Formacces,
    map_from_schema,
    map_to_schema,
)
from formshare.config.encdecdata import encode_data
import logging

log = logging.getLogger("formshare")


class odata(plugins.SingletonPlugin):
    plugins.implements(plugins.IRoutes)
    plugins.implements(plugins.IConfig)
    plugins.implements(plugins.ITranslation)
    plugins.implements(plugins.ISchema)
    plugins.implements(plugins.IResource)

    # Implements IResource
    def add_libraries(self, config):
        libraries = [u.add_library("odata", "resources")]
        return libraries

    def add_js_resources(self, config):
        odata_js = [
            u.add_js_resource("odata", "footable", "footable/js/footable.min.js", None)
        ]
        return odata_js

    def add_css_resources(self, config):
        odata_css = [
            u.add_css_resource(
                "odata", "footable", "footable/css/footable.bootstrap.min.css", None
            )
        ]
        return odata_css

    # Implements IRoutes
    def before_mapping(self, config):
        # We don't add any routes before the host application
        return []

    def after_mapping(self, config):
        # We add here a new route /json that returns a JSON
        custom_map = [
            u.add_route(
                "odata_generate",
                "/user/{userid}/project/{projcode}/form/{formid}/odata/generate",
                ODataGenerateView,
                None,
            ),
            u.add_route(
                "odata_check",
                "/user/{userid}/project/{projcode}/form/{formid}/odata/check",
                ODataCheckView,
                None,
            ),
            u.add_route(
                "odata_users",
                "/user/{userid}/project/{projcode}/form/{formid}/odata/users",
                ODataUsersView,
                "odata/users_and_groups.jinja2",
            ),
            u.add_route(
                "odata_change_access",
                "/user/{userid}/project/{projcode}/form/{formid}/odata/change",
                ODataChangeAccessView,
                None,
            ),
            u.add_route(
                "odata_table_access",
                "/user/{userid}/project/{projcode}/form/{formid}/odata/tables/{odatauser}",
                ODataTableAccessView,
                "odata/user_access.jinja2",
            ),
            u.add_route(
                "odata_table_action",
                "/user/{userid}/project/{projcode}/form/{formid}/odata/tables/{odatauser}/action",
                ODataActionView,
                "json",
            ),
        ]

        return custom_map

    def update_config(self, config):
        # We add here the templates of the plugin to the config
        u.add_templates_directory(config, "templates")

    def get_translation_directory(self):
        module = sys.modules["odata"]
        return os.path.join(os.path.dirname(module.__file__), "locale")

    def get_translation_domain(self):
        return "odata"

    # Implements ISchema. This will include a field called OData as part of a form DB schema
    def update_schema(self, config):
        return [
            u.add_field_to_form_schema("odata_artifact", "Artifact ID"),
            u.add_field_to_form_schema("odata_request", "Request ID"),
            u.add_field_to_form_schema("odata_status", "Request status"),
            u.add_field_to_form_schema("odata_url_v2", "Version 2 URL"),
            u.add_field_to_form_schema("odata_url_v4", "Version 4 URL"),
            u.add_field_to_form_access_schema(
                "odata_access", "Whether the assistant has OData access"
            ),
        ]


class ODataAccess(plugins.SingletonPlugin):
    plugins.implements(plugins.IAssistant)
    plugins.implements(plugins.IFormAccess)
    plugins.implements(plugins.ITranslation)

    def get_translation_directory(self):
        module = sys.modules["odata"]
        return os.path.join(os.path.dirname(module.__file__), "locale")

    def get_translation_domain(self):
        return "odata"

    # Implements IAssistant
    def before_creating_assistant(self, request, user, project, assistant_data):
        return assistant_data, True, ""

    def after_creating_assistant(self, request, user, project, assistant_data):
        pass

    def before_editing_assistant(
        self, request, user, project, assistant, assistant_data
    ):
        return assistant_data, True, ""

    def after_editing_assistant(
        self, request, user, project, assistant, assistant_data
    ):
        if assistant_data.get("coll_active", 0) == 0:
            res = (
                request.dbsession.query(
                    Odkform.form_id,
                    Odkform.project_id,
                    Odkform.form_schema,
                    Odkform.extras,
                )
                .filter(Odkform.project_id == Formacces.form_project)
                .filter(Odkform.form_id == Formacces.form_id)
                .filter(Formacces.project_id == project)
                .filter(Formacces.coll_id == assistant)
                .filter(Odkform.form_schema.isnot(None))
                .filter(Odkform.extras.isnot(None))
                .all()
            )
            mapped_data = map_from_schema(res)
            if mapped_data:
                for an_assistant in mapped_data:
                    if an_assistant.get("odata_status", 0) == 1:
                        sql = "DELETE FROM {}.odatauser WHERE user_name = '{}'".format(
                            an_assistant["form_schema"], assistant
                        )
                        try:
                            request.dbsession.execute(sql)
                            mapped_data = map_to_schema(
                                Formacces, {"odata_access": "0"}
                            )
                            request.dbsession.query(Formacces).filter(
                                Formacces.project_id == project
                            ).filter(Formacces.coll_id == assistant).filter(
                                Formacces.form_project == an_assistant["project_id"]
                            ).filter(
                                Formacces.form_id == an_assistant["form_id"]
                            ).update(
                                mapped_data
                            )

                        except Exception as e:
                            log.error("ODataAccess Error: {}".format(str(e)))

    def before_deleting_assistant(self, request, user, project, assistant):
        res = (
            request.dbsession.query(Odkform.form_schema, Odkform.extras)
            .filter(Odkform.project_id == Formacces.form_project)
            .filter(Odkform.form_id == Formacces.form_id)
            .filter(Formacces.project_id == project)
            .filter(Formacces.coll_id == assistant)
            .filter(Odkform.form_schema.isnot(None))
            .filter(Odkform.extras.isnot(None))
            .all()
        )
        mapped_data = map_from_schema(res)
        can_delete = True
        if mapped_data:
            for an_assistant in mapped_data:
                if an_assistant.get("odata_status", 0) == 1:
                    sql = "SELECT count(*) FROM {}.odatauser WHERE user_name = '{}'".format(
                        an_assistant["form_schema"], assistant
                    )
                    try:
                        res = request.dbsession.execute(sql).fetchone()
                        if res[0] != 0:
                            can_delete = False
                            break
                    except Exception as e:
                        log.error("ODataAccess Error: {}".format(str(e)))
        if can_delete:
            return True, ""
        else:
            _ = request.translate
            return (
                False,
                _(
                    "This assistant has an OData access and cannot be deleted. "
                    "Deactivate it first before deleting it"
                ),
            )

    def after_deleting_assistant(self, request, user, project, assistant):
        pass

    def before_assistant_password_change(
        self, request, user, project, assistant, password
    ):
        return True, ""

    def after_assistant_password_change(
        self, request, user, project, assistant, password
    ):
        res = (
            request.dbsession.query(Odkform.form_schema, Odkform.extras)
            .filter(Odkform.project_id == Formacces.form_project)
            .filter(Odkform.form_id == Formacces.form_id)
            .filter(Formacces.project_id == project)
            .filter(Formacces.coll_id == assistant)
            .filter(Odkform.form_schema.isnot(None))
            .filter(Odkform.extras.isnot(None))
            .all()
        )
        mapped_data = map_from_schema(res)
        if mapped_data:
            encrypted_password = encode_data(request, password)
            for an_assistant in mapped_data:
                if an_assistant.get("odata_status", 0) == 1:
                    sql = "UPDATE {}.odatauser SET user_password = '{}' WHERE user_name = '{}'".format(
                        an_assistant["form_schema"],
                        encrypted_password.decode(),
                        assistant,
                    )
                    try:
                        request.dbsession.execute(sql)
                    except Exception as e:
                        log.error("ODataAccess Error: {}".format(str(e)))

    # Implements IFormAccess

    def before_giving_access_to_assistant(
        self,
        request,
        user,
        project,
        form,
        assistant_project,
        assistant_id,
        privilege_data,
    ):
        res = (
            request.dbsession.query(Odkform.form_schema, Odkform.extras)
            .filter(Odkform.project_id == project)
            .filter(Odkform.form_id == form)
            .filter(Odkform.form_schema.isnot(None))
            .filter(Odkform.extras.isnot(None))
            .first()
        )
        mapped_data = map_from_schema(res)
        if mapped_data:
            if mapped_data.get("odata_status", 0) == 1:
                privilege_data["odata_access"] = 0
        return privilege_data, True, ""

    def after_giving_access_to_assistant(
        self,
        request,
        user,
        project,
        form,
        assistant_project,
        assistant_id,
        privilege_data,
    ):
        pass

    def before_editing_assistant_access(
        self,
        request,
        user,
        project,
        form,
        assistant_project,
        assistant_id,
        privilege_data,
    ):
        res = (
            request.dbsession.query(Odkform.form_schema, Odkform.extras)
            .filter(Odkform.project_id == project)
            .filter(Odkform.form_id == form)
            .filter(Odkform.form_schema.isnot(None))
            .filter(Odkform.extras.isnot(None))
            .first()
        )
        mapped_data = map_from_schema(res)
        if mapped_data:
            if mapped_data.get("odata_status", 0) == 1:
                if privilege_data["coll_can_clean"] == "1":
                    privilege_data["odata_access"] = 0
        return privilege_data, True, ""

    def after_editing_assistant_access(
        self,
        request,
        user,
        project,
        form,
        assistant_project,
        assistant_id,
        privilege_data,
    ):
        res = (
            request.dbsession.query(Odkform.form_schema, Odkform.extras)
            .filter(Odkform.project_id == project)
            .filter(Odkform.form_id == form)
            .filter(Odkform.form_schema.isnot(None))
            .filter(Odkform.extras.isnot(None))
            .first()
        )
        mapped_data = map_from_schema(res)
        if mapped_data:
            if mapped_data.get("odata_status", 0) == 1:
                if privilege_data["coll_can_clean"] == "1":
                    sql = "DELETE FROM {}.odatauser WHERE user_name = '{}'".format(
                        mapped_data["form_schema"], assistant_id
                    )
                    try:
                        request.dbsession.execute(sql)
                    except Exception as e:
                        log.error("ODataAccess Error: {}".format(str(e)))

    def before_revoking_assistant_access(
        self, request, user, project, form, assistant_project, assistant_id
    ):
        return True, ""

    def after_revoking_assistant_access(
        self, request, user, project, form, assistant_project, assistant_id
    ):
        res = (
            request.dbsession.query(Odkform.form_schema, Odkform.extras)
            .filter(Odkform.project_id == project)
            .filter(Odkform.form_id == form)
            .filter(Odkform.form_schema.isnot(None))
            .filter(Odkform.extras.isnot(None))
            .first()
        )
        mapped_data = map_from_schema(res)
        if mapped_data:
            if mapped_data.get("odata_status", 0) == 1:
                sql = "DELETE FROM {}.odatauser WHERE user_name = '{}'".format(
                    mapped_data["form_schema"], assistant_id
                )
                try:
                    request.dbsession.execute(sql)
                except Exception as e:
                    log.error("ODataAccess Error: {}".format(str(e)))
