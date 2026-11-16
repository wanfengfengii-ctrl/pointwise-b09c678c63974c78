class WarehouseError(Exception):
    code = "WarehouseError"


class SchemaError(WarehouseError):
    code = "SchemaError"


class DataError(WarehouseError):
    code = "DataError"


class ResolutionError(WarehouseError):
    code = "ResolutionError"


class Conflict(WarehouseError):
    code = "Conflict"


class NotFound(WarehouseError):
    code = "NotFound"


class NotReady(WarehouseError):
    code = "NotReady"
