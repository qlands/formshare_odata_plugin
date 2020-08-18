from formshare.plugins.interfaces import Interface


class IWARFileCreated(Interface):
    """
    Plugin into the creation of the WarFile.

    """

    def after_create(self, request, absolute_path):
        """
        Called after the OData WAR files are created.

        :param request: Pyramid request object
        :param absolute_path: Absolute path to the WAR file created
        :return True, or False whether the process was successful or not
        """
        raise NotImplementedError("after_create must be implemented in subclasses")
