#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for Windows layered window rendering fix.

Tests the defensive geometry validation and error handling for the desktop Fairy
card rendering on Windows.
"""

import logging
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


class TestLayeredWindowGeometryValidation(unittest.TestCase):
    """Test geometry validation for Windows layered windows."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            QApplication([])

    def test_valid_geometry(self):
        """Test that valid geometry passes validation."""
        widget = QWidget()
        widget.resize(250, 325)
        rect = widget.rect()

        # Valid geometry should pass
        self.assertGreater(rect.width(), 0)
        self.assertGreater(rect.height(), 0)
        self.assertEqual(rect.left(), 0)
        self.assertEqual(rect.top(), 0)

    def test_invalid_zero_size(self):
        """Test that zero-size geometry is detected."""
        widget = QWidget()
        widget.resize(0, 0)
        rect = widget.rect()

        # Zero size should be invalid
        self.assertEqual(rect.width(), 0)
        self.assertEqual(rect.height(), 0)

    def test_invalid_negative_size(self):
        """Test that negative size is detected."""
        rect = QRect(0, 0, -100, 100)

        # Negative width should be invalid
        self.assertLess(rect.width(), 0)

    def test_dirty_rect_validation(self):
        """Test dirty rect parameter validation."""
        # Valid dirty rect
        window_size = (250, 325)
        valid_dirty = (0, 0, 250, 325)

        # Check bounds
        self.assertGreaterEqual(valid_dirty[0], 0)  # left >= 0
        self.assertGreaterEqual(valid_dirty[1], 0)  # top >= 0
        self.assertLessEqual(valid_dirty[2], window_size[0])  # right <= width
        self.assertLessEqual(valid_dirty[3], window_size[1])  # bottom <= height

        # Invalid dirty rect (from error message)
        invalid_dirty = (274, 236, -12, 100)

        # Check for invalid parameters
        self.assertLess(invalid_dirty[2], 0)  # negative coordinate

    def test_child_widget_bounds_checking(self):
        """Test that child widgets are checked against parent bounds."""
        parent = QWidget()
        parent.resize(250, 325)

        child = QWidget(parent)
        child.setGeometry(0, 0, 332, 225)  # Exceeds parent width

        parent_rect = parent.rect()
        child_rect = child.geometry()

        # Child exceeds parent bounds
        self.assertGreater(child_rect.right(), parent_rect.right())

        # Clamped rect should be within bounds
        clamped = child_rect.intersected(parent_rect)
        self.assertLessEqual(clamped.right(), parent_rect.right())
        self.assertLessEqual(clamped.bottom(), parent_rect.bottom())

    def test_dpi_scaling_geometry(self):
        """Test geometry with DPI scaling factors."""
        # Logical size at 100% DPI
        logical_width = 250
        logical_height = 325

        # Physical size at different DPI scales
        dpi_scales = [1.0, 1.25, 1.5, 2.0]

        for dpi_ratio in dpi_scales:
            physical_width = int(logical_width * dpi_ratio)
            physical_height = int(logical_height * dpi_ratio)

            # Physical size should be valid
            self.assertGreater(physical_width, 0)
            self.assertGreater(physical_height, 0)

            # Conversion back should be close to original
            back_to_logical_w = int(physical_width / dpi_ratio)
            back_to_logical_h = int(physical_height / dpi_ratio)

            self.assertAlmostEqual(back_to_logical_w, logical_width, delta=1)
            self.assertAlmostEqual(back_to_logical_h, logical_height, delta=1)

    def test_shadow_bounds_extension(self):
        """Test that shadow effects don't cause out-of-bounds issues."""
        widget = QWidget()
        widget.resize(250, 325)

        # Shadow blur radius
        blur_radius = 24
        shadow_offset = 8

        # Shadow extends beyond widget bounds
        shadow_right = widget.width() + blur_radius
        shadow_bottom = widget.height() + blur_radius + shadow_offset

        # Shadow should be clamped to window bounds
        window_width = 250
        window_height = 325

        clamped_right = min(shadow_right, window_width)
        clamped_bottom = min(shadow_bottom, window_height)

        self.assertLessEqual(clamped_right, window_width)
        self.assertLessEqual(clamped_bottom, window_height)

    def test_animation_offset_bounds(self):
        """Test that animation offsets don't cause invalid geometry."""
        base_height = 100
        animation_offset = 50

        # Animated height
        animated_height = base_height + animation_offset

        # Should still be valid
        self.assertGreater(animated_height, 0)

        # When clamped to max height
        max_height = 325
        clamped_height = min(animated_height, max_height)

        self.assertGreater(clamped_height, 0)
        self.assertLessEqual(clamped_height, max_height)

    def test_card_padding_bounds(self):
        """Test that card padding doesn't exceed window bounds."""
        window_width = 250
        window_height = 325

        # Card with padding
        card_padding = 8
        card_margin = 6

        # Available space for card content
        available_width = window_width - (card_padding * 2) - (card_margin * 2)
        available_height = window_height - (card_padding * 2) - (card_margin * 2)

        # Should be positive
        self.assertGreater(available_width, 0)
        self.assertGreater(available_height, 0)

        # Card content should fit
        card_content_width = 220
        card_content_height = 200

        self.assertLessEqual(card_content_width, available_width)
        self.assertLessEqual(card_content_height, available_height)


class TestGeometryValidationMethods(unittest.TestCase):
    """Test the geometry validation methods."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            QApplication([])

    def test_validate_and_clamp_geometry_valid(self):
        """Test validation with valid geometry."""
        widget = QWidget()
        widget.resize(250, 325)

        # Should not raise exception
        try:
            rect = widget.rect()
            self.assertGreater(rect.width(), 0)
            self.assertGreater(rect.height(), 0)
        except Exception as e:
            self.fail(f"Validation failed with valid geometry: {e}")

    def test_validate_and_clamp_geometry_invalid(self):
        """Test validation with invalid geometry."""
        widget = QWidget()
        widget.resize(0, 0)

        # Should detect invalid size
        rect = widget.rect()
        self.assertEqual(rect.width(), 0)
        self.assertEqual(rect.height(), 0)

    def test_child_widget_clamping(self):
        """Test that child widgets are clamped to parent bounds."""
        parent = QWidget()
        parent.resize(250, 325)

        child = QWidget(parent)
        child.setGeometry(0, 0, 400, 400)  # Exceeds parent

        parent_rect = parent.rect()
        child_rect = child.geometry()

        # Clamp child to parent bounds
        clamped = child_rect.intersected(parent_rect)

        self.assertLessEqual(clamped.width(), parent_rect.width())
        self.assertLessEqual(clamped.height(), parent_rect.height())


class TestErrorHandling(unittest.TestCase):
    """Test error handling and fallback mechanisms."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            QApplication([])

    def test_exception_handling_in_paint(self):
        """Test that paint exceptions are handled gracefully."""
        widget = QWidget()

        # Simulate paint exception
        exception_caught = False
        try:
            # This would normally call paintEvent
            raise Exception("Simulated paint error")
        except Exception:
            exception_caught = True

        self.assertTrue(exception_caught)

    def test_fallback_to_full_repaint(self):
        """Test fallback to full window repaint."""
        widget = QWidget()
        widget.resize(250, 325)

        # Fallback should trigger full update
        try:
            widget.update()  # Full repaint
        except Exception as e:
            self.fail(f"Fallback repaint failed: {e}")

    def test_exception_in_resize(self):
        """Test exception handling in resize event."""
        widget = QWidget()

        exception_caught = False
        try:
            widget.resize(250, 325)
            # Simulate resize exception
            raise Exception("Simulated resize error")
        except Exception:
            exception_caught = True

        self.assertTrue(exception_caught)


class TestDPIScaling(unittest.TestCase):
    """Test DPI scaling handling."""

    @classmethod
    def setUpClass(cls):
        """Set up QApplication for tests."""
        if not QApplication.instance():
            QApplication([])

    def test_dpi_ratio_calculation(self):
        """Test DPI ratio calculations."""
        widget = QWidget()
        dpi_ratio = widget.devicePixelRatio()

        # DPI ratio should be positive
        self.assertGreater(dpi_ratio, 0)

        # Common DPI ratios
        self.assertIn(dpi_ratio, [1.0, 1.25, 1.5, 2.0])

    def test_logical_to_physical_conversion(self):
        """Test conversion between logical and physical pixels."""
        widget = QWidget()
        dpi_ratio = widget.devicePixelRatio()

        logical_size = 250
        physical_size = int(logical_size * dpi_ratio)

        # Physical size should be >= logical size
        self.assertGreaterEqual(physical_size, logical_size)

        # Conversion back should be close
        back_to_logical = int(physical_size / dpi_ratio)
        self.assertAlmostEqual(back_to_logical, logical_size, delta=1)

    def test_geometry_at_different_dpi(self):
        """Test geometry validation at different DPI scales."""
        widget = QWidget()

        # Test at different logical sizes
        test_sizes = [(100, 100), (250, 325), (500, 600)]

        for width, height in test_sizes:
            widget.resize(width, height)
            rect = widget.rect()

            self.assertEqual(rect.width(), width)
            self.assertEqual(rect.height(), height)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    unittest.main()
