import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from accept_production_activation import host_checks_for_format, plugin_validation_timeout


class FormatHostChecksTests(unittest.TestCase):
    def test_complete_bank_budget_includes_every_authored_image(self):
        self.assertEqual(plugin_validation_timeout({}), 180)
        self.assertEqual(plugin_validation_timeout({'factoryParameterPresets': [{}] * 2000}), 2180)
        self.assertEqual(plugin_validation_timeout({'parameterStateImages': [{}] * 16}), 196)

    def test_state_vectors_stay_separate_from_a_factory_bank(self):
        checks = {'parameterStateImages': [{'name': 'Default', 'values': {'Gain': 0}}],
                  'stateImageFormatParameters': {'VST3': {'Bypass': 0}}}
        actual = host_checks_for_format(checks, 'VST3')
        self.assertEqual(actual['parameterStateImages'][0]['values'], {'Gain': 0, 'Bypass': 0})
        self.assertNotIn('factoryParameterPresets', actual)
        self.assertEqual(host_checks_for_format(checks, 'AudioUnit')['parameterStateImages'][0]['values'], {'Gain': 0})

    def test_wrapper_parameter_is_format_specific_and_does_not_mutate_input(self):
        checks = {'factoryParameterPresets': [{'name': 'A', 'values': {'Mix': .5}}],
                  'factoryImageFormatParameters': {'VST3': {'Bypass': 0}}}
        original = copy.deepcopy(checks)
        self.assertEqual(host_checks_for_format(checks, 'VST3')['factoryParameterPresets'][0]['values'],
                         {'Mix': .5, 'Bypass': 0})
        self.assertEqual(host_checks_for_format(checks, 'AudioUnit')['factoryParameterPresets'][0]['values'], {'Mix': .5})
        self.assertEqual(checks, original)

    def test_format_parameter_cannot_replace_authored_value(self):
        checks = {'factoryParameterPresets': [{'values': {'Mix': .5}}],
                  'factoryImageFormatParameters': {'VST3': {'Mix': 0}}}
        with self.assertRaises(ValueError):
            host_checks_for_format(checks, 'VST3')

    def test_unbounded_format_parameter_is_rejected(self):
        checks = {'factoryParameterPresets': [{'values': {'Mix': .5}}],
                  'factoryImageFormatParameters': {'VST3': {'Bypass': 2}}}
        with self.assertRaises(ValueError):
            host_checks_for_format(checks, 'VST3')


if __name__ == '__main__':
    unittest.main()
