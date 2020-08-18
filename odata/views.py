from formshare.plugins.utilities import FormShareFormEditorView, FormShareFormAdminView
from formshare.processes.db import (
    get_form_schema,
    get_form_xml_create_file,
    get_form_details,
    get_assistant_password,
    get_form_directory,
)
from formshare.models import (
    Odkform,
    map_to_schema,
    map_from_schema,
    Formacces,
    Collaborator,
)
from formshare.processes.submission.api import get_tables_from_form
from pyramid.httpexceptions import HTTPNotFound, HTTPFound, HTTPBadRequest
import requests
import os
import json
from subprocess import Popen, PIPE
import logging
import zipfile
from pyramid.response import Response
from pathlib import Path
import uuid
import time
import random
import redis
from zope.sqlalchemy import mark_changed
from odata.plugins.interfaces import IWARFileCreated
import formshare.plugins as p
from formshare.processes.odk import get_odk_path

log = logging.getLogger("formshare")


class ODataGenerateView(FormShareFormEditorView):
    def process_view(self):
        FormShareFormEditorView.process_view(self)
        random_delay = random.randint(1, 5)
        time.sleep(random_delay)

        redis_host = self.request.registry.settings.get(
            "redis.sessions.host", "localhost"
        )
        redis_port = int(
            self.request.registry.settings.get("redis.sessions.port", "6379")
        )
        r = redis.Redis(host=redis_host, port=redis_port)
        redis_key = "odata-{}-{}".format(self.project_id, self.form_id)

        res = (
            self.request.dbsession.query(Odkform)
            .filter(Odkform.project_id == self.project_id)
            .filter(Odkform.form_id == self.form_id)
            .first()
        )
        res = map_from_schema(res)

        def update_odata_status(artifact, status, request):
            r.mset({redis_key: status})
            update_data = {
                "odata_artifact": artifact,
                "odata_status": status,
                "odata_request": request,
            }

            mapped_data = map_to_schema(Odkform, update_data)
            self.request.dbsession.query(Odkform).filter(
                Odkform.project_id == self.project_id
            ).filter(Odkform.form_id == self.form_id).update(mapped_data)

        self.returnRawViewResult = True
        if r.get(redis_key) is None:
            current_db_status = res.get("odata_status", None)
            if current_db_status is not None:
                r.mset({redis_key: current_db_status})
            else:
                r.mset({redis_key: 0})

        if int(r.get(redis_key)) == 0 or int(r.get(redis_key)) == -1:
            form_schema = get_form_schema(self.request, self.project_id, self.form_id)
            if form_schema is None:
                r.delete(redis_key)
                raise HTTPNotFound
            xml_file = get_form_xml_create_file(
                self.request, self.project_id, self.form_id
            )
            if xml_file is None:
                odk_dir = get_odk_path(self.request)
                form_directory = get_form_directory(self.request, self.project_id, self.form_id)
                xml_file = os.path.join(
                    odk_dir, *["forms", form_directory, "repository", "create.xml"]
                )

            if not os.path.exists(xml_file):
                r.delete(redis_key)
                raise HTTPNotFound
            mysql_host = self.request.registry.settings.get("mysql.host")
            mysql_port = self.request.registry.settings.get("mysql.port", "3306")
            mysql_user = self.request.registry.settings.get("mysql.user")
            mysql_password = self.request.registry.settings.get("mysql.password")
            odata_generator_url = self.request.registry.settings.get(
                "odata.generator.url"
            )
            odata_generator_key = self.request.registry.settings.get(
                "odata.generator.key"
            )
            odata_generator_password = self.request.registry.settings.get(
                "odata.generator.password"
            )
            odata_encryption_key = self.request.registry.settings.get(
                "aes.key"
            )
            artifact_id = form_schema[-12:]
            request_id = str(uuid.uuid4())
            files = {"xml": open(xml_file, "rb")}
            payload = {
                "request_id": request_id,
                "key": odata_generator_key,
                "key_password": odata_generator_password,
                "schema": form_schema,
                "artifact": artifact_id,
                "host": mysql_host,
                "port": mysql_port,
                "user": mysql_user,
                "password": mysql_password,
                "encryption_key": odata_encryption_key,
            }

            update_odata_status(artifact_id, 2, request_id)
            try:
                response = requests.post(
                    odata_generator_url + "/create",
                    files=files,
                    data=payload,
                    verify=True,
                )
            except Exception as e:
                update_odata_status(artifact_id, -1, request_id)
                next_page = self.request.route_url(
                    "form_details",
                    userid=self.user_id,
                    projcode=self.project_code,
                    formid=self.form_id,
                )
                self.add_error(
                    self._(
                        "FormShare was not able to contact the Odata generator server. "
                        "The QLands technical support "
                        "has been informed about this."
                    )
                )
                log.error(
                    "Error while requesting the OData generator to process for database {}. Error: {}".format(
                        form_schema, str(e)
                    )
                )
                return HTTPFound(next_page, headers={"FS_error": "true"})

            if response.status_code == 200:
                odata_generator_schema_file = self.request.registry.settings.get(
                    "odata.generator.schema.file"
                )
                cnf_file = self.request.registry.settings.get("mysql.cnf")
                args = ["mysql", "--defaults-file=" + cnf_file, form_schema]
                with open(odata_generator_schema_file) as input_file:
                    proc = Popen(args, stdin=input_file, stderr=PIPE, stdout=PIPE)
                    output, error = proc.communicate()
                    if proc.returncode == 0:
                        next_page = self.request.route_url(
                            "form_details",
                            userid=self.user_id,
                            projcode=self.project_code,
                            formid=self.form_id,
                        )
                        self.request.session.flash(
                            self._(
                                "FormShare is generating the OData resource. It will be ready soon..."
                            )
                        )
                        return HTTPFound(next_page)
                    else:
                        update_odata_status(artifact_id, -1, request_id)
                        error_message = "Error creating OData tables \n"
                        error_message = (
                            error_message
                            + "File: "
                            + odata_generator_schema_file
                            + "\n"
                        )
                        error_message = error_message + "Error: \n"
                        if error is not None:
                            error_message = error_message + error.decode() + "\n"
                        error_message = error_message + "Output: \n"
                        if output is not None:
                            error_message = error_message + output.decode() + "\n"
                        log.error(error_message)
                        next_page = self.request.route_url(
                            "form_details",
                            userid=self.user_id,
                            projcode=self.project_code,
                            formid=self.form_id,
                        )
                        self.add_error(
                            self._(
                                "FormShare was not able to generate the OData resource. "
                                "The QLands technical support "
                                "has been informed about this."
                            )
                        )
                        log.error(
                            "Error while creating the OData control tables for database {}".format(
                                form_schema
                            )
                        )
                        return HTTPFound(next_page, headers={"FS_error": "true"})
            else:
                update_odata_status(artifact_id, -1, request_id)
                next_page = self.request.route_url(
                    "form_details",
                    userid=self.user_id,
                    projcode=self.project_code,
                    formid=self.form_id,
                )
                self.add_error(
                    self._(
                        "FormShare was not able to generate the OData resource. The QLands technical support "
                        "has been informed about this."
                    )
                )
                log.error(
                    "Requesting the OData generator to process for database {} returned error {}".format(
                        form_schema, response.status_code
                    )
                )
                return HTTPFound(next_page, headers={"FS_error": "true"})

        else:
            if int(r.get(redis_key)) >= 2:
                next_page = self.request.route_url(
                    "form_details",
                    userid=self.user_id,
                    projcode=self.project_code,
                    formid=self.form_id,
                )
                self.request.session.flash(
                    self._(
                        "FormShare is generating the OData resource. It will be ready soon..."
                    )
                )
                return HTTPFound(next_page)
            else:
                next_page = self.request.route_url(
                    "form_details",
                    userid=self.user_id,
                    projcode=self.project_code,
                    formid=self.form_id,
                )
                self.request.session.flash(
                    self._("The OData resource is ready. Enjoy!")
                )
                return HTTPFound(next_page)


class ODataCheckView(FormShareFormEditorView):
    def process_view(self):
        self.returnRawViewResult = True
        FormShareFormEditorView.process_view(self)

        random_delay = random.randint(1, 5)
        time.sleep(random_delay)
        redis_host = self.request.registry.settings.get(
            "redis.sessions.host", "localhost"
        )
        redis_port = int(
            self.request.registry.settings.get("redis.sessions.port", "6379")
        )
        r = redis.Redis(host=redis_host, port=redis_port)
        redis_key = "odata-{}-{}".format(self.project_id, self.form_id)

        def update_status(status):
            r.mset({redis_key: status})
            status_data = {
                "odata_status": status,
            }
            mapped_status = map_to_schema(Odkform, status_data)
            self.request.dbsession.query(Odkform).filter(
                Odkform.project_id == self.project_id
            ).filter(Odkform.form_id == self.form_id).update(mapped_status)
            self.request.dbsession.flush()

        result_type = self.request.params.get("result_type", "web")
        form_details = get_form_details(
            self.request, self.user_id, self.project_id, self.form_id
        )
        if r.get(redis_key) is None:
            r.mset({redis_key: 0})

        if int(r.get(redis_key)) == 2:
            r.mset({redis_key: 3})  # Checking
            odata_request = form_details["odata_request"]
            artifact_id = form_details["odata_artifact"]
            odata_generator_url = self.request.registry.settings.get(
                "odata.generator.url"
            )
            odata_generator_key = self.request.registry.settings.get(
                "odata.generator.key"
            )
            odata_generator_password = self.request.registry.settings.get(
                "odata.generator.password"
            )
            odata_war_directory = self.request.registry.settings.get(
                "odata.war.directory"
            )
            payload = {
                "key": odata_generator_key,
                "request": odata_request,
                "key_password": odata_generator_password,
            }

            try:
                response = requests.post(
                    odata_generator_url + "/check", data=payload, verify=True
                )
            except Exception as e:
                update_status(-1)
                log.error(
                    "Error while contacting the OData generator server for request {}. Error: {}".format(
                        odata_request, str(e)
                    )
                )
                if result_type == "web":
                    next_page = self.request.route_url(
                        "form_details",
                        userid=self.user_id,
                        projcode=self.project_code,
                        formid=self.form_id,
                    )
                    self.add_error(
                        self._(
                            "FormShare was not able to contact the OData generator server. "
                            "The QLands technical support has been informed about this."
                        )
                    )
                    return HTTPFound(next_page, headers={"FS_error": "true"})
                else:
                    headers = [("Content-Type", "application/json")]
                    return Response(
                        json_body={"result": 500}, status=200, headerlist=headers
                    )

            if response.status_code == 200:
                json_response = json.loads(response.content)

                if json_response["request_status"] == -1:
                    update_status(2)
                    if result_type == "web":
                        next_page = self.request.route_url(
                            "form_details",
                            userid=self.user_id,
                            projcode=self.project_code,
                            formid=self.form_id,
                        )
                        self.request.session.flash(
                            self._(
                                "FormShare is still generating the OData resource. It will be ready soon..."
                            )
                        )
                        return HTTPFound(next_page)
                    else:
                        headers = [("Content-Type", "application/json")]
                        return Response(
                            json_body={"result": 201}, status=200, headerlist=headers,
                        )
                else:
                    if json_response["request_status"] == 0:
                        if int(r.get(redis_key)) == 3:
                            repository_path = self.request.registry.settings.get(
                                "repository.path"
                            )

                            paths = ["tmp", "odata", odata_request]
                            temp_dir = os.path.join(repository_path, *paths)

                            paths = ["V2" + artifact_id + ".war"]
                            deployed_war_file = os.path.join(
                                odata_war_directory, *paths
                            )

                            paths = ["V4" + artifact_id + ".war"]
                            deployed_war_file_v4 = os.path.join(
                                odata_war_directory, *paths
                            )

                            if not os.path.exists(temp_dir):
                                os.makedirs(temp_dir)

                            paths = ["tmp", "odata", odata_request, "wars.zip"]
                            war_zip_file = os.path.join(repository_path, *paths)
                            if os.path.exists(war_zip_file):
                                os.remove(war_zip_file)
                            Path(war_zip_file).touch()
                            try:
                                response = requests.post(
                                    odata_generator_url + "/download",
                                    data=payload,
                                    verify=True,
                                )
                            except Exception as e:
                                update_status(-1)
                                if os.path.exists(deployed_war_file):
                                    os.remove(deployed_war_file)
                                if os.path.exists(deployed_war_file_v4):
                                    os.remove(deployed_war_file_v4)
                                if os.path.exists(war_zip_file):
                                    os.remove(war_zip_file)
                                log.error(
                                    "Error while contacting the OData generator "
                                    "server for request {}. Error: {}".format(
                                        odata_request, str(e)
                                    )
                                )
                                if result_type == "web":
                                    next_page = self.request.route_url(
                                        "form_details",
                                        userid=self.user_id,
                                        projcode=self.project_code,
                                        formid=self.form_id,
                                    )
                                    self.add_error(
                                        self._(
                                            "FormShare was not able to contact the OData generator server. "
                                            "The QLands technical support has been informed about this."
                                        )
                                    )
                                    return HTTPFound(
                                        next_page, headers={"FS_error": "true"}
                                    )
                                else:
                                    headers = [("Content-Type", "application/json")]
                                    return Response(
                                        json_body={"result": 500},
                                        status=200,
                                        headerlist=headers,
                                    )
                            if response.status_code == 200:
                                try:
                                    # Deploy V2
                                    if os.path.exists(deployed_war_file):
                                        os.remove(deployed_war_file)
                                    open(war_zip_file, "wb").write(response.content)
                                    with zipfile.ZipFile(war_zip_file, "r") as zip_ref:
                                        zip_ref.extract(
                                            "V2" + artifact_id + ".war",
                                            odata_war_directory,
                                        )
                                    plugin_results = []
                                    for plugin in p.PluginImplementations(
                                        IWARFileCreated
                                    ):
                                        plugin_results.append(
                                            plugin.after_create(
                                                self.request, deployed_war_file
                                            )
                                        )

                                    # Deploy V4
                                    if os.path.exists(deployed_war_file_v4):
                                        os.remove(deployed_war_file_v4)
                                    open(war_zip_file, "wb").write(response.content)
                                    with zipfile.ZipFile(war_zip_file, "r") as zip_ref:
                                        zip_ref.extract(
                                            "V4" + artifact_id + ".war",
                                            odata_war_directory,
                                        )
                                    plugin_results4 = []
                                    for plugin in p.PluginImplementations(
                                        IWARFileCreated
                                    ):
                                        plugin_results4.append(
                                            plugin.after_create(
                                                self.request, deployed_war_file_v4,
                                            )
                                        )

                                    if all(plugin_results) and all(plugin_results4):
                                        odata_service_url_v2 = (
                                            self.request.registry.settings.get(
                                                "odata.service.url"
                                            )
                                            + "/"
                                            + "V2"
                                            + artifact_id
                                            + "/service.svc"
                                        )
                                        odata_service_url_v4 = (
                                            self.request.registry.settings.get(
                                                "odata.service.url"
                                            )
                                            + "/"
                                            + "V4"
                                            + artifact_id
                                            + "/service.svc"
                                        )
                                        update_status(1)
                                        update_data = {
                                            "odata_artifact": artifact_id,
                                            "odata_url_v2": odata_service_url_v2,
                                            "odata_url_v4": odata_service_url_v4,
                                        }
                                        mapped_data = map_to_schema(
                                            Odkform, update_data
                                        )
                                        self.request.dbsession.query(Odkform).filter(
                                            Odkform.project_id == self.project_id
                                        ).filter(
                                            Odkform.form_id == self.form_id
                                        ).update(
                                            mapped_data
                                        )
                                        self.request.dbsession.flush()

                                        if result_type == "web":
                                            next_page = self.request.route_url(
                                                "form_details",
                                                userid=self.user_id,
                                                projcode=self.project_code,
                                                formid=self.form_id,
                                            )
                                            self.request.session.flash(
                                                self._(
                                                    "The OData resource is ready. Enjoy!"
                                                )
                                            )
                                            return HTTPFound(next_page)
                                        else:
                                            headers = [
                                                ("Content-Type", "application/json",)
                                            ]
                                            return Response(
                                                json_body={"result": 200},
                                                status=200,
                                                headerlist=headers,
                                            )
                                    else:
                                        update_status(-1)
                                        log.error(
                                            "Error in plugin deployment for request {}.".format(
                                                odata_request
                                            )
                                        )
                                        # if os.path.exists(deployed_war_file):
                                        #     os.remove(deployed_war_file)

                                        # if os.path.exists(deployed_war_file_v4):
                                        #     os.remove(deployed_war_file_v4)

                                        if os.path.exists(war_zip_file):
                                            os.remove(war_zip_file)

                                        if result_type == "web":
                                            next_page = self.request.route_url(
                                                "form_details",
                                                userid=self.user_id,
                                                projcode=self.project_code,
                                                formid=self.form_id,
                                            )
                                            self.add_error(
                                                self._(
                                                    "The process generated an error. The QLands technical "
                                                    "support has been informed about this."
                                                )
                                            )
                                            return HTTPFound(
                                                next_page, headers={"FS_error": "true"},
                                            )
                                        else:
                                            headers = [
                                                ("Content-Type", "application/json",)
                                            ]
                                            return Response(
                                                json_body={"result": 500},
                                                status=200,
                                                headerlist=headers,
                                            )

                                except Exception as e:
                                    update_status(-1)
                                    log.error(
                                        "Error while unzipping the war file from request {}. Error: {}".format(
                                            odata_request, str(e)
                                        )
                                    )
                                    if os.path.exists(deployed_war_file):
                                        os.remove(deployed_war_file)

                                    if os.path.exists(deployed_war_file_v4):
                                        os.remove(deployed_war_file_v4)

                                    if os.path.exists(war_zip_file):
                                        os.remove(war_zip_file)

                                    if result_type == "web":
                                        next_page = self.request.route_url(
                                            "form_details",
                                            userid=self.user_id,
                                            projcode=self.project_code,
                                            formid=self.form_id,
                                        )
                                        self.add_error(
                                            self._(
                                                "The process generated an error. The QLands technical support "
                                                "has been informed about this."
                                            )
                                        )
                                        return HTTPFound(
                                            next_page, headers={"FS_error": "true"},
                                        )
                                    else:
                                        headers = [("Content-Type", "application/json")]
                                        return Response(
                                            json_body={"result": 500},
                                            status=200,
                                            headerlist=headers,
                                        )
                            else:
                                update_status(-1)
                                if os.path.exists(deployed_war_file):
                                    os.remove(deployed_war_file)

                                if os.path.exists(deployed_war_file_v4):
                                    os.remove(deployed_war_file_v4)

                                if os.path.exists(war_zip_file):
                                    os.remove(war_zip_file)
                                log.error(
                                    "Error while downloading the OData resource for request {}".format(
                                        odata_request
                                    )
                                )
                                if result_type == "web":
                                    next_page = self.request.route_url(
                                        "form_details",
                                        userid=self.user_id,
                                        projcode=self.project_code,
                                        formid=self.form_id,
                                    )
                                    self.add_error(
                                        self._(
                                            "The process generated an error. The QLands technical support "
                                            "has been informed about this."
                                        )
                                    )
                                    return HTTPFound(
                                        next_page, headers={"FS_error": "true"}
                                    )
                                else:
                                    headers = [("Content-Type", "application/json")]
                                    return Response(
                                        json_body={"result": 500},
                                        status=200,
                                        headerlist=headers,
                                    )
                        else:
                            if int(r.get(redis_key)) != -1:
                                if result_type == "web":
                                    next_page = self.request.route_url(
                                        "form_details",
                                        userid=self.user_id,
                                        projcode=self.project_code,
                                        formid=self.form_id,
                                    )
                                    self.request.session.flash(
                                        self._(
                                            "FormShare is still generating the OData resource. "
                                            "It will be ready soon..."
                                        )
                                    )
                                    return HTTPFound(next_page)
                                else:
                                    headers = [("Content-Type", "application/json")]
                                    return Response(
                                        json_body={"result": 201},
                                        status=200,
                                        headerlist=headers,
                                    )
                            else:
                                if result_type == "web":
                                    next_page = self.request.route_url(
                                        "form_details",
                                        userid=self.user_id,
                                        projcode=self.project_code,
                                        formid=self.form_id,
                                    )
                                    self.add_error(
                                        self._(
                                            "The process generated an error. The QLands technical support "
                                            "has been informed about this."
                                        )
                                    )
                                    return HTTPFound(
                                        next_page, headers={"FS_error": "true"}
                                    )
                                else:
                                    headers = [("Content-Type", "application/json")]
                                    return Response(
                                        json_body={"result": 500},
                                        status=200,
                                        headerlist=headers,
                                    )
                    else:
                        log.error(
                            "Error in created the OData resource for request {}".format(
                                odata_request
                            )
                        )
                        update_status(-1)
                        if result_type == "web":
                            next_page = self.request.route_url(
                                "form_details",
                                userid=self.user_id,
                                projcode=self.project_code,
                                formid=self.form_id,
                            )
                            self.add_error(
                                self._(
                                    "The process generated an error. The QLands technical support "
                                    "has been informed about this."
                                )
                            )
                            return HTTPFound(next_page, headers={"FS_error": "true"})
                        else:
                            headers = [("Content-Type", "application/json")]
                            return Response(
                                json_body={"result": 500},
                                status=200,
                                headerlist=headers,
                            )
            else:
                update_status(-1)
                log.error(
                    "Error while requesting the OData generator for the status of request {}".format(
                        odata_request
                    )
                )
                if result_type == "web":
                    next_page = self.request.route_url(
                        "form_details",
                        userid=self.user_id,
                        projcode=self.project_code,
                        formid=self.form_id,
                    )
                    self.add_error(
                        self._(
                            "FormShare was not able check the status OData resource. The QLands technical support "
                            "has been informed about this."
                        )
                    )
                    return HTTPFound(next_page, headers={"FS_error": "true"})
                else:
                    headers = [("Content-Type", "application/json")]
                    return Response(
                        json_body={"result": 500}, status=200, headerlist=headers
                    )
        else:
            if int(r.get(redis_key)) == 1:
                if result_type == "web":
                    next_page = self.request.route_url(
                        "form_details",
                        userid=self.user_id,
                        projcode=self.project_code,
                        formid=self.form_id,
                    )
                    self.request.session.flash(
                        self._("The OData resource is ready. Enjoy!")
                    )
                    return HTTPFound(next_page)
                else:
                    headers = [("Content-Type", "application/json")]
                    return Response(
                        json_body={"result": 200}, status=200, headerlist=headers,
                    )
            else:
                if int(r.get(redis_key)) == -1:
                    if result_type == "web":
                        next_page = self.request.route_url(
                            "form_details",
                            userid=self.user_id,
                            projcode=self.project_code,
                            formid=self.form_id,
                        )
                        self.add_error(
                            self._(
                                "The process generated an error. The QLands technical support "
                                "has been informed about this."
                            )
                        )
                        return HTTPFound(next_page, headers={"FS_error": "true"})
                    else:
                        headers = [("Content-Type", "application/json")]
                        return Response(
                            json_body={"result": 500}, status=200, headerlist=headers,
                        )
                else:
                    if result_type == "web":
                        next_page = self.request.route_url(
                            "form_details",
                            userid=self.user_id,
                            projcode=self.project_code,
                            formid=self.form_id,
                        )
                        self.request.session.flash(
                            self._(
                                "FormShare is still generating the OData resource. "
                                "It will be ready soon..."
                            )
                        )
                        return HTTPFound(next_page)
                    else:
                        headers = [("Content-Type", "application/json")]
                        return Response(
                            json_body={"result": 201}, status=200, headerlist=headers,
                        )


class ODataUsersView(FormShareFormAdminView):
    def process_view(self):
        FormShareFormAdminView.process_view(self)
        form_schema = get_form_schema(self.request, self.project_id, self.form_id)
        if form_schema is None:
            raise HTTPNotFound
        if self.form_details.get("odata_status", 0) != 1:
            raise HTTPNotFound
        res = (
            self.request.dbsession.query(
                Collaborator.coll_id,
                Collaborator.coll_name,
                Collaborator.coll_email,
                Collaborator.project_id,
                Formacces.extras,
            )
            .filter(Formacces.project_id == Collaborator.project_id)
            .filter(Formacces.coll_id == Collaborator.coll_id)
            .filter(Formacces.form_project == self.project_id)
            .filter(Formacces.form_id == self.form_id)
            .filter(Collaborator.coll_active == 1)
            .filter(Formacces.coll_privileges > 1)
            .all()
        )

        assistants = map_from_schema(res)
        groups = self.request.dbsession.execute(
            "SELECT {0}.odatagroup.group_id, group_name, count(user_name) as members "
            "FROM {0}.odatagroup,{0}.odatagroupuser "
            "WHERE {0}.odatagroup.group_id = {0}.odatagroupuser.group_id "
            "GROUP BY {0}.odatagroup.group_id, group_name".format(form_schema)
        ).fetchall()

        return {"assistants": assistants, "groups": groups}


class ODataChangeAccessView(FormShareFormAdminView):
    def __init__(self, request):
        FormShareFormAdminView.__init__(self, request)
        self.checkCrossPost = False

    def process_view(self):
        FormShareFormAdminView.process_view(self)
        self.returnRawViewResult = True
        if self.request.method == "POST":
            form_schema = get_form_schema(self.request, self.project_id, self.form_id)
            if form_schema is None:
                raise HTTPNotFound
            if self.form_details.get("odata_status", 0) == 0:
                raise HTTPNotFound

            assistant_data = self.get_post_dict()
            mapped_data = map_to_schema(Formacces, assistant_data)
            if assistant_data["odata_access"] == "0":
                sql = "DELETE FROM {}.odatauser WHERE user_name = '{}'".format(
                    form_schema, assistant_data["coll_id"]
                )
                try:
                    self.request.dbsession.execute(sql)

                    self.request.dbsession.query(Formacces).filter(
                        Formacces.project_id == assistant_data["project_id"]
                    ).filter(Formacces.coll_id == assistant_data["coll_id"]).filter(
                        Formacces.form_project == self.project_id
                    ).filter(
                        Formacces.form_id == self.form_id
                    ).update(
                        mapped_data
                    )

                except Exception as e:
                    self.add_error(
                        self._(
                            "Error at revoking OData access. QLands has been informed"
                        )
                    )
                    log.error("ODataAccess Error: {}".format(str(e)))
                return HTTPFound(
                    self.request.route_url(
                        "odata_users",
                        userid=self.user_id,
                        projcode=self.project_code,
                        formid=self.form_id,
                    )
                )
            else:
                assistant_password = get_assistant_password(
                    self.request,
                    self.user_id,
                    assistant_data["project_id"],
                    assistant_data["coll_id"],
                    False,
                )
                sql = "INSERT INTO {}.odatauser (user_name,user_password) VALUES ('{}','{}')".format(
                    form_schema, assistant_data["coll_id"], assistant_password
                )
                try:
                    self.request.dbsession.execute(sql)

                    self.request.dbsession.query(Formacces).filter(
                        Formacces.project_id == assistant_data["project_id"]
                    ).filter(Formacces.coll_id == assistant_data["coll_id"]).filter(
                        Formacces.form_project == self.project_id
                    ).filter(
                        Formacces.form_id == self.form_id
                    ).update(
                        mapped_data
                    )

                except Exception as e:
                    self.add_error(
                        self._(
                            "Error at granting OData access. QLands has been informed"
                        )
                    )
                    log.error("ODataAccess Error: {}".format(str(e)))
                return HTTPFound(
                    self.request.route_url(
                        "odata_users",
                        userid=self.user_id,
                        projcode=self.project_code,
                        formid=self.form_id,
                    )
                )

        else:
            raise HTTPNotFound


class ODataTableAccessView(FormShareFormAdminView):
    def process_view(self):
        FormShareFormAdminView.process_view(self)
        odata_user = self.request.matchdict["odatauser"]
        form_schema = get_form_schema(self.request, self.project_id, self.form_id)

        if form_schema is None:
            raise HTTPNotFound
        if self.form_details.get("odata_status", 0) == 0:
            raise HTTPNotFound

        if self.request.method == "GET":
            assistant = (
                self.request.dbsession.query(Collaborator)
                .filter(Collaborator.project_id == self.project_id)
                .filter(Collaborator.coll_id == odata_user)
                .first()
            )
            assistant_data = map_from_schema(assistant)
            if assistant_data:
                sql = (
                    "SELECT table_name,allow_select,allow_insert,allow_update,allow_delete "
                    "FROM {}.odatauseraccess WHERE user_name = '{}'".format(
                        form_schema, odata_user
                    )
                )
                used_tables = self.request.dbsession.execute(sql).fetchall()
                tables = get_tables_from_form(
                    self.request, self.project_id, self.form_id
                )
                for a_table in tables:
                    a_table["odata_select"] = False
                    a_table["odata_insert"] = False
                    a_table["odata_update"] = False
                    a_table["odata_delete"] = False
                if used_tables is not None:
                    for a_used_table in used_tables:
                        for a_table in tables:
                            if a_used_table[0] == a_table["name"]:
                                a_table["odata_select"] = True
                                if a_used_table[2] == 1:
                                    a_table["odata_insert"] = True
                                else:
                                    a_table["odata_insert"] = False
                                if a_used_table[3] == 1:
                                    a_table["odata_update"] = True
                                else:
                                    a_table["odata_update"] = False
                                if a_used_table[4] == 1:
                                    a_table["odata_delete"] = True
                                else:
                                    a_table["odata_delete"] = False

                return {"assistant": assistant_data, "tables": tables}
            else:
                raise HTTPNotFound
        else:
            action_data = self.get_post_dict()
            if "grant-all" in action_data.keys():
                tables = get_tables_from_form(
                    self.request, self.project_id, self.form_id
                )
                for a_table in tables:
                    try:
                        sql = (
                            "INSERT INTO {}.odatauseraccess (user_name,table_name,allow_select) "
                            "VALUES ('{}','{}',1)".format(
                                form_schema, odata_user, a_table["name"]
                            )
                        )
                        self.request.dbsession.execute(sql)
                    except Exception as e:
                        log.error(
                            "Error while grating OData select access "
                            "on schema {} to user {} for table {}. Error: {}".format(
                                form_schema, odata_user, a_table["name"], str(e),
                            )
                        )
                mark_changed(self.request.dbsession)

            if "revoke-all" in action_data.keys():
                try:
                    sql = "DELETE FROM {}.odatauseraccess WHERE user_name = '{}'".format(
                        form_schema, odata_user
                    )
                    self.request.dbsession.execute(sql)
                    mark_changed(self.request.dbsession)
                except Exception as e:
                    log.error(
                        "Error while revoking OData select access to all tables "
                        "on schema {} to user {}. Error: {}".format(
                            form_schema, odata_user, str(e),
                        )
                    )

            self.returnRawViewResult = True
            return HTTPFound(self.request.url)


class ODataActionView(FormShareFormAdminView):
    def __init__(self, request):
        FormShareFormAdminView.__init__(self, request)
        self.checkCrossPost = False

    def process_view(self):
        FormShareFormAdminView.process_view(self)
        self.returnRawViewResult = True
        if self.request.method == "POST":
            odata_user = self.request.matchdict["odatauser"]
            form_schema = get_form_schema(self.request, self.project_id, self.form_id)

            if form_schema is None:
                raise HTTPNotFound
            if self.form_details.get("odata_status", 0) == 0:
                raise HTTPNotFound

            action_data = self.get_post_dict()
            if (
                action_data.get("table", None) is not None
                and action_data.get("action", None) is not None
                and action_data.get("grant", None) is not None
            ):
                if action_data.get("action") == "grant":
                    if action_data.get("grant") == "select":
                        sql = (
                            "INSERT INTO {}.odatauseraccess (user_name,table_name,allow_select) "
                            "VALUES ('{}','{}',1)".format(
                                form_schema, odata_user, action_data.get("table")
                            )
                        )
                        try:
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                        except Exception as e:
                            log.error(
                                "Error while grating OData select access "
                                "on schema {} to user {} for table {}. Error: {}".format(
                                    form_schema,
                                    odata_user,
                                    action_data.get("table"),
                                    str(e),
                                )
                            )
                            raise HTTPBadRequest
                    else:
                        if action_data.get("grant") == "update":
                            sql = (
                                "UPDATE {}.odatauseraccess SET allow_update = 1 "
                                "WHERE user_name = '{}' AND table_name = '{}'".format(
                                    form_schema, odata_user, action_data.get("table")
                                )
                            )
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                        if action_data.get("grant") == "insert":
                            sql = (
                                "UPDATE {}.odatauseraccess SET allow_insert = 1 "
                                "WHERE user_name = '{}' AND table_name = '{}'".format(
                                    form_schema, odata_user, action_data.get("table")
                                )
                            )
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                        if action_data.get("grant") == "delete":
                            sql = (
                                "UPDATE {}.odatauseraccess SET allow_delete = 1 "
                                "WHERE user_name = '{}' AND table_name = '{}'".format(
                                    form_schema, odata_user, action_data.get("table")
                                )
                            )
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                else:
                    if action_data.get("grant") == "select":
                        sql = "DELETE FROM {}.odatauseraccess WHERE user_name = '{}' AND table_name = '{}'".format(
                            form_schema, odata_user, action_data.get("table")
                        )
                        self.request.dbsession.execute(sql)
                        mark_changed(self.request.dbsession)
                    else:
                        if action_data.get("grant") == "update":
                            sql = (
                                "UPDATE {}.odatauseraccess SET allow_update = 0 "
                                "WHERE user_name = '{}' AND table_name = '{}'".format(
                                    form_schema, odata_user, action_data.get("table")
                                )
                            )
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                        if action_data.get("grant") == "insert":
                            sql = (
                                "UPDATE {}.odatauseraccess SET allow_insert = 0 "
                                "WHERE user_name = '{}' AND table_name = '{}'".format(
                                    form_schema, odata_user, action_data.get("table")
                                )
                            )
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                        if action_data.get("grant") == "delete":
                            sql = (
                                "UPDATE {}.odatauseraccess SET allow_delete = 0 "
                                "WHERE user_name = '{}' AND table_name = '{}'".format(
                                    form_schema, odata_user, action_data.get("table")
                                )
                            )
                            self.request.dbsession.execute(sql)
                            mark_changed(self.request.dbsession)
                return {}
            else:
                raise HTTPNotFound
        else:
            raise HTTPNotFound
