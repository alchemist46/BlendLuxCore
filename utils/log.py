import logging

# The addon's own messages. Its level follows the "Show LuxCore Logs"
# preference (see set_verbose()); scripts can change it, or add handlers, with
# logging.getLogger("blendluxcore").
logger = logging.getLogger("blendluxcore")
# Messages of the LuxCore core
core_logger = logging.getLogger("blendluxcore.luxcore")


class _ConsoleHandler(logging.Handler):
    """Print to the console, where print() used to write."""
    def emit(self, record):
        try:
            print(self.format(record))
        except Exception:
            self.handleError(record)


class _Formatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        if record.name == core_logger.name:
            # LuxCore messages carry their own prefix
            return message
        if record.levelno >= logging.WARNING:
            return f"[BLC] {record.levelname}: {message}"
        return f"[BLC] {message}"


def set_verbose(verbose):
    logger.setLevel(logging.INFO if verbose else logging.WARNING)


# On script reload, replace the handler of the previous version of this module
for _handler in logger.handlers[:]:
    if getattr(_handler, "is_blendluxcore", False):
        logger.removeHandler(_handler)
_handler = _ConsoleHandler()
_handler.is_blendluxcore = True
_handler.setFormatter(_Formatter())
logger.addHandler(_handler)
logger.propagate = False
if logger.level == logging.NOTSET:
    set_verbose(True)


class LuxCoreLog:
    _listeners = []

    @staticmethod
    def add(msg):
        core_logger.info(msg)

        for listener in LuxCoreLog._listeners:
            listener(msg)

    @staticmethod
    def silent(msg):
        pass

    @classmethod
    def add_listener(cls, listener):
        cls._listeners.append(listener)

    @classmethod
    def remove_listener(cls, listener):
        cls._listeners.remove(listener)
