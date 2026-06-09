"""
Animation Utilities - Professional animations for UI transitions

Provides smooth, modern animations for:
- Fade in/out
- Slide in/out
- Expand/collapse
- Smooth scrolling
- Hover effects
"""

from PyQt6.QtCore import (
    QPropertyAnimation, QEasingCurve, QAbstractAnimation,
    QParallelAnimationGroup, QSequentialAnimationGroup, Qt, QPoint, QSize
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget
from PyQt6.QtGui import QColor


class AnimationHelper:
    """
    Helper class for creating smooth animations.
    
    Provides pre-configured animations for common UI transitions.
    """
    
    # Easing curves for different effects
    EASE_IN_OUT = QEasingCurve.Type.InOutCubic
    EASE_OUT = QEasingCurve.Type.OutCubic
    EASE_IN = QEasingCurve.Type.InCubic
    BOUNCE = QEasingCurve.Type.OutBounce
    ELASTIC = QEasingCurve.Type.OutElastic
    
    @staticmethod
    def fade_in(
        widget: QWidget,
        duration: int = 300,
        easing: QEasingCurve.Type = EASE_OUT,
        on_finished=None
    ) -> QPropertyAnimation:
        """
        Fade in animation.
        
        Args:
            widget: Widget to animate
            duration: Animation duration in ms
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QPropertyAnimation instance
        """
        # Create opacity effect if not exists
        if not widget.graphicsEffect():
            opacity_effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(opacity_effect)
        else:
            opacity_effect = widget.graphicsEffect()
        
        # Create animation
        anim = QPropertyAnimation(opacity_effect, b"opacity")
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(easing)
        
        if on_finished:
            anim.finished.connect(on_finished)
        
        return anim
    
    @staticmethod
    def fade_out(
        widget: QWidget,
        duration: int = 300,
        easing: QEasingCurve.Type = EASE_IN,
        on_finished=None
    ) -> QPropertyAnimation:
        """
        Fade out animation.
        
        Args:
            widget: Widget to animate
            duration: Animation duration in ms
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QPropertyAnimation instance
        """
        # Create opacity effect if not exists
        if not widget.graphicsEffect():
            opacity_effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(opacity_effect)
        else:
            opacity_effect = widget.graphicsEffect()
        
        # Create animation
        anim = QPropertyAnimation(opacity_effect, b"opacity")
        anim.setDuration(duration)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(easing)
        
        if on_finished:
            anim.finished.connect(on_finished)
        
        return anim
    
    @staticmethod
    def slide_in(
        widget: QWidget,
        direction: str = "left",
        duration: int = 400,
        distance: int = None,
        easing: QEasingCurve.Type = EASE_OUT,
        on_finished=None
    ) -> QPropertyAnimation:
        """
        Slide in animation.
        
        Args:
            widget: Widget to animate
            direction: Direction to slide from (left, right, top, bottom)
            duration: Animation duration in ms
            distance: Slide distance (default: widget width/height)
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QPropertyAnimation instance
        """
        # Calculate positions
        current_pos = widget.pos()
        
        if distance is None:
            distance = widget.width() if direction in ("left", "right") else widget.height()
        
        if direction == "left":
            start_pos = QPoint(current_pos.x() - distance, current_pos.y())
        elif direction == "right":
            start_pos = QPoint(current_pos.x() + distance, current_pos.y())
        elif direction == "top":
            start_pos = QPoint(current_pos.x(), current_pos.y() - distance)
        else:  # bottom
            start_pos = QPoint(current_pos.x(), current_pos.y() + distance)
        
        # Create animation
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(duration)
        anim.setStartValue(start_pos)
        anim.setEndValue(current_pos)
        anim.setEasingCurve(easing)
        
        if on_finished:
            anim.finished.connect(on_finished)
        
        return anim
    
    @staticmethod
    def slide_out(
        widget: QWidget,
        direction: str = "left",
        duration: int = 400,
        distance: int = None,
        easing: QEasingCurve.Type = EASE_IN,
        on_finished=None
    ) -> QPropertyAnimation:
        """
        Slide out animation.
        
        Args:
            widget: Widget to animate
            direction: Direction to slide to (left, right, top, bottom)
            duration: Animation duration in ms
            distance: Slide distance (default: widget width/height)
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QPropertyAnimation instance
        """
        # Calculate positions
        current_pos = widget.pos()
        
        if distance is None:
            distance = widget.width() if direction in ("left", "right") else widget.height()
        
        if direction == "left":
            end_pos = QPoint(current_pos.x() - distance, current_pos.y())
        elif direction == "right":
            end_pos = QPoint(current_pos.x() + distance, current_pos.y())
        elif direction == "top":
            end_pos = QPoint(current_pos.x(), current_pos.y() - distance)
        else:  # bottom
            end_pos = QPoint(current_pos.x(), current_pos.y() + distance)
        
        # Create animation
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(duration)
        anim.setStartValue(current_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(easing)
        
        if on_finished:
            anim.finished.connect(on_finished)
        
        return anim
    
    @staticmethod
    def expand(
        widget: QWidget,
        target_height: int,
        duration: int = 300,
        easing: QEasingCurve.Type = EASE_OUT,
        on_finished=None
    ) -> QPropertyAnimation:
        """
        Expand animation (height).
        
        Args:
            widget: Widget to animate
            target_height: Target height
            duration: Animation duration in ms
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QPropertyAnimation instance
        """
        anim = QPropertyAnimation(widget, b"maximumHeight")
        anim.setDuration(duration)
        anim.setStartValue(0)
        anim.setEndValue(target_height)
        anim.setEasingCurve(easing)
        
        if on_finished:
            anim.finished.connect(on_finished)
        
        return anim
    
    @staticmethod
    def collapse(
        widget: QWidget,
        duration: int = 300,
        easing: QEasingCurve.Type = EASE_IN,
        on_finished=None
    ) -> QPropertyAnimation:
        """
        Collapse animation (height).
        
        Args:
            widget: Widget to animate
            duration: Animation duration in ms
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QPropertyAnimation instance
        """
        current_height = widget.height()
        
        anim = QPropertyAnimation(widget, b"maximumHeight")
        anim.setDuration(duration)
        anim.setStartValue(current_height)
        anim.setEndValue(0)
        anim.setEasingCurve(easing)
        
        if on_finished:
            anim.finished.connect(on_finished)
        
        return anim
    
    @staticmethod
    def scale(
        widget: QWidget,
        scale_factor: float,
        duration: int = 300,
        easing: QEasingCurve.Type = EASE_OUT,
        on_finished=None
    ) -> QParallelAnimationGroup:
        """
        Scale animation.
        
        Args:
            widget: Widget to animate
            scale_factor: Scale factor (1.0 = original, 2.0 = double size)
            duration: Animation duration in ms
            easing: Easing curve
            on_finished: Callback when animation finishes
        
        Returns:
            QParallelAnimationGroup instance
        """
        current_size = widget.size()
        target_size = QSize(
            int(current_size.width() * scale_factor),
            int(current_size.height() * scale_factor)
        )
        
        # Animate both width and height
        group = QParallelAnimationGroup()
        
        width_anim = QPropertyAnimation(widget, b"minimumWidth")
        width_anim.setDuration(duration)
        width_anim.setStartValue(current_size.width())
        width_anim.setEndValue(target_size.width())
        width_anim.setEasingCurve(easing)
        
        height_anim = QPropertyAnimation(widget, b"minimumHeight")
        height_anim.setDuration(duration)
        height_anim.setStartValue(current_size.height())
        height_anim.setEndValue(target_size.height())
        height_anim.setEasingCurve(easing)
        
        group.addAnimation(width_anim)
        group.addAnimation(height_anim)
        
        if on_finished:
            group.finished.connect(on_finished)
        
        return group
    
    @staticmethod
    def fade_and_slide_in(
        widget: QWidget,
        direction: str = "left",
        duration: int = 400,
        on_finished=None
    ) -> QParallelAnimationGroup:
        """
        Combined fade + slide in animation.
        
        Args:
            widget: Widget to animate
            direction: Direction to slide from
            duration: Animation duration in ms
            on_finished: Callback when animation finishes
        
        Returns:
            QParallelAnimationGroup instance
        """
        group = QParallelAnimationGroup()
        
        fade = AnimationHelper.fade_in(widget, duration)
        slide = AnimationHelper.slide_in(widget, direction, duration)
        
        group.addAnimation(fade)
        group.addAnimation(slide)
        
        if on_finished:
            group.finished.connect(on_finished)
        
        return group
    
    @staticmethod
    def fade_and_slide_out(
        widget: QWidget,
        direction: str = "right",
        duration: int = 400,
        on_finished=None
    ) -> QParallelAnimationGroup:
        """
        Combined fade + slide out animation.
        
        Args:
            widget: Widget to animate
            direction: Direction to slide to
            duration: Animation duration in ms
            on_finished: Callback when animation finishes
        
        Returns:
            QParallelAnimationGroup instance
        """
        group = QParallelAnimationGroup()
        
        fade = AnimationHelper.fade_out(widget, duration)
        slide = AnimationHelper.slide_out(widget, direction, duration)
        
        group.addAnimation(fade)
        group.addAnimation(slide)
        
        if on_finished:
            group.finished.connect(on_finished)
        
        return group


class AnimatedWidget(QWidget):
    """
    Widget with built-in animation methods.
    
    Convenience class that makes it easy to animate any widget.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._animations = []
    
    def fade_in(self, duration: int = 300):
        """Fade in this widget."""
        anim = AnimationHelper.fade_in(self, duration)
        self._animations.append(anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    
    def fade_out(self, duration: int = 300, on_finished=None):
        """Fade out this widget."""
        anim = AnimationHelper.fade_out(self, duration, on_finished=on_finished)
        self._animations.append(anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    
    def slide_in(self, direction: str = "left", duration: int = 400):
        """Slide in this widget."""
        anim = AnimationHelper.slide_in(self, direction, duration)
        self._animations.append(anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    
    def slide_out(self, direction: str = "right", duration: int = 400, on_finished=None):
        """Slide out this widget."""
        anim = AnimationHelper.slide_out(self, direction, duration, on_finished=on_finished)
        self._animations.append(anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    
    def expand_animated(self, target_height: int, duration: int = 300):
        """Expand this widget."""
        anim = AnimationHelper.expand(self, target_height, duration)
        self._animations.append(anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    
    def collapse_animated(self, duration: int = 300, on_finished=None):
        """Collapse this widget."""
        anim = AnimationHelper.collapse(self, duration, on_finished=on_finished)
        self._animations.append(anim)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
