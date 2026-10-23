"""Run full pluginval audio, API and editor validation on the installed bundle."""

def arguments(product, pluginval, bundle, production_editor_already_tested=False):
    return [str(pluginval), '--strictness-level', '5', '--validate', str(bundle)]
