class BacktestError(Exception):
    """Base error with a stable process exit category."""


class ConfigError(BacktestError):
    pass


class DataError(BacktestError):
    pass


class StrategyError(BacktestError):
    pass


class SimulationError(BacktestError):
    pass


class ReportError(BacktestError):
    pass
