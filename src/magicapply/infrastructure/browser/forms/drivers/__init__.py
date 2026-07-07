"""Form field drivers."""

from magicapply.infrastructure.browser.forms.drivers.hybrid import HybridDriver
from magicapply.infrastructure.browser.forms.drivers.llm import LLMDriver
from magicapply.infrastructure.browser.forms.drivers.protocol import FormFieldDriver
from magicapply.infrastructure.browser.forms.drivers.rules import RulesBasedDriver

__all__ = ["FormFieldDriver", "HybridDriver", "LLMDriver", "RulesBasedDriver"]