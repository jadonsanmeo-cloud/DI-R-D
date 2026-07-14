import unittest


class SpecModuleLayoutTests(unittest.TestCase):
    def test_spec_package_exports_spec_building_components(self) -> None:
        from data_intelligence_sdk.spec import (
            ClusterExecutionSpec,
            DataSelectionPrompt,
            DefaultClusterSpecBuilder,
            LLMClusterSpecSelector,
            LLMDataSelector,
            SelectedDataContext,
            SpecBuilderPrompt,
            SpecBuildContext,
            SpecContextBuilder,
        )

        self.assertEqual(ClusterExecutionSpec.__name__, "ClusterExecutionSpec")
        self.assertEqual(DefaultClusterSpecBuilder.__name__, "DefaultClusterSpecBuilder")
        self.assertEqual(LLMClusterSpecSelector.__name__, "LLMClusterSpecSelector")
        self.assertEqual(SpecContextBuilder.__name__, "SpecContextBuilder")
        self.assertEqual(SpecBuildContext.__name__, "SpecBuildContext")
        self.assertEqual(SelectedDataContext.__name__, "SelectedDataContext")
        self.assertEqual(LLMDataSelector.__name__, "LLMDataSelector")
        self.assertEqual(DataSelectionPrompt.__name__, "DataSelectionPrompt")
        self.assertEqual(SpecBuilderPrompt.__name__, "SpecBuilderPrompt")

    def test_spec_subpackages_own_implementation_import_paths(self) -> None:
        from data_intelligence_sdk.spec.context import SpecContextBuilder
        from data_intelligence_sdk.spec.data_selection import (
            LLMDataSelector,
            SelectedDataContext,
        )
        from data_intelligence_sdk.spec.prompts import (
            DataSelectionPrompt,
            SpecBuilderPrompt,
        )

        self.assertEqual(SpecContextBuilder.__module__, "data_intelligence_sdk.spec.context")
        self.assertEqual(
            SelectedDataContext.__module__,
            "data_intelligence_sdk.spec.data_selection.types",
        )
        self.assertEqual(
            LLMDataSelector.__module__,
            "data_intelligence_sdk.spec.data_selection.selector",
        )
        self.assertEqual(
            DataSelectionPrompt.__module__,
            "data_intelligence_sdk.spec.prompts.data_selection",
        )
        self.assertEqual(
            SpecBuilderPrompt.__module__,
            "data_intelligence_sdk.spec.prompts.spec_builder",
        )

    def test_old_import_paths_remain_compatibility_wrappers(self) -> None:
        from data_intelligence_sdk.context import SpecContextBuilder as OldContextBuilder
        from data_intelligence_sdk.data_selection import (
            LLMDataSelector as OldDataSelector,
        )
        from data_intelligence_sdk.prompts import SpecBuilderPrompt as OldPrompt
        from data_intelligence_sdk.spec import (
            LLMDataSelector,
            SpecBuilderPrompt,
            SpecContextBuilder,
        )

        self.assertIs(OldContextBuilder, SpecContextBuilder)
        self.assertIs(OldDataSelector, LLMDataSelector)
        self.assertIs(OldPrompt, SpecBuilderPrompt)


if __name__ == "__main__":
    unittest.main()
