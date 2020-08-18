FormShare OData Plug-in
==============

This FormShare plug-in creates [OData](https://www.odata.org/) services for any data repository in FormShare.

The plug-in relies on [OData Generator Service](https://services.formshare.org/odata_generator), a private service by QLands that generates OData V2 and V4 services as WAR files. The plug-in sends the repository structure created by FormShare to the Generator Service and when the WAR files are ready (a WAR for OData V2 and one for OData V4) download them to a local directory (odata.war.directory setting).

The final deployment of the WAR files to make them available through a Tomcat or Jetty server (odata.service.url setting) **must be** developed by creating a deployment plug-in (see example below). The WAR files connect with the FormShare repository using JDBC therefore the MySQL server used by FormShare **must be** reachable by the Tomcat or Jetty server.

The OData services authenticate users using Basic Authentication therefore your Tomcat or Jetty server MUST run over HTTPS.

Getting Started
---------------

- Clone this repository

  ```shell
  git clone https://github.com/qlands/formshare_odata_plugin.git
  ```

- Activate the FormShare environment.

  ```shell
  . ./path/to/FormShare_environment/bin/activate
  ```
- Change directory to the plug-in.

  ```shell
  cd formshare_odata_plugin
  ```
- Build the plug-in

  ```shell
  python setup.py develop
  python setup.py compile_catalog
  ```
- Add the plug-in to the FormShare list of plug-ins in development.ini. Also add and edit the plug-in settings

  ```ini
  #formshare.plugins = examplePlugin
  formshare.plugins = odata odata_deployment
      
  # OData plug-in settings
  odata.generator.url = https://services.formshare.org/odata_generator
  odata.generator.key = Get one by emailing info@qlands.com
  odata.generator.password = Get one by emailing info@qlands.com
  odata.generator.schema.file = /absolute_path_to/formshare_odata_plugin/db/odata_control_tables.sql
  odata.service.url = https://my_tomcat_server.com
  odata.war.directory = /absolute_path_to_a_directory_to_store_the_wars
  ```
- Create a deployment plug-in

  Follow the recipe [here](https://github.com/qlands/formshare-cookiecutter-plugin) and call it "odata_deployment".

- Edit the file plugin.py of the plug-in to add the below code. You can remove the other extension classes added to the plug-in by default

  ```python
  from odata.plugins.interfaces import IWARFileCreated
  
  class MyCustomODataDeploy(plugins.SingletonPlugin):
      plugins.implements(IWARFileCreated)
  
      def after_create(self, request, absolute_path):
          # request = The request object from FormShare. You can use it to read settings from the ini file
          # absolute_path = Absolute path to the WAR file
          # This process will be called twice. One for the V2 WAR and one for the V4 WAR.
          # Do somethig with the WAR file. For example, copy it to the webapps directory of Tomcat
          
  ```

  

- Run FormShare again

