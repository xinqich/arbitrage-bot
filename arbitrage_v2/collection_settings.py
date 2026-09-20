"""Collection controls; old cycle/lifetime request caps are deliberately ignored."""
from copy import deepcopy

DEFAULTS = {
    'collection_interval_seconds': 3600,
    'research_batch_size': 300,
    'research_refresh_seconds': 3600,
    'freshness_seconds': 14400,
    'max_run_seconds': 3600,
    'request_spacing_seconds': {'steam_public': 5, 'dmarket': 2, 'steamapis': 30},
    'retry_seconds': [60, 300, 900],
    'rate_limit_cooldown_seconds': 900,
}


def settings(config=None):
    result = deepcopy(DEFAULTS)
    config = config or {}
    result.update(config)
    result['request_spacing_seconds'] = dict(DEFAULTS['request_spacing_seconds'],
                                            **config.get('request_spacing_seconds', {}))
    for name in ('collection_interval_seconds', 'research_batch_size', 'research_refresh_seconds',
                 'freshness_seconds', 'max_run_seconds', 'rate_limit_cooldown_seconds'):
        if type(result[name]) is not int or result[name] <= 0:
            raise ValueError('invalid collection setting: ' + name)
    for value in result['request_spacing_seconds'].values():
        if type(value) not in (int, float) or not 0 <= value < float('inf'):
            raise ValueError('invalid request spacing')
    if (not isinstance(result['retry_seconds'], list) or len(result['retry_seconds']) > 10
            or any(type(v) is not int or v <= 0 for v in result['retry_seconds'])):
        raise ValueError('invalid retry delays')
    return result
