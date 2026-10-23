"""Keep full audio/API validation while avoiding one demonstrated CI GUI hang."""
def arguments(product, pluginval, bundle, production_editor_already_tested=False):
    result=[str(pluginval),'--strictness-level','5']
    # Inflated Ego's licensed editor passes the production host twice and all
    # 111 authored programs, but pluginval on hosted Windows hangs on GUI
    # shutdown after SUCCESS. Use pluginval's documented headless CI option
    # only when those independent installed-editor tests have just passed.
    if product['slug']=='inflated-ego'and production_editor_already_tested:
        result.append('--skip-gui-tests')
    return [*result,'--validate',str(bundle)]
