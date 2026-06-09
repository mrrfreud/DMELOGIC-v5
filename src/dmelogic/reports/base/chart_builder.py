"""
Chart Builder - Generate professional charts for reports

Creates matplotlib charts with consistent styling and export capabilities.
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import io

try:
    import matplotlib
    matplotlib.use('Qt5Agg')  # Use Qt backend
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    Figure = None
    FigureCanvas = None


class ChartBuilder:
    """
    Professional chart generation for reports.

    Supports:
    - Line charts (trends over time)
    - Bar charts (comparisons)
    - Pie charts (distributions)
    - Stacked bar charts
    - Multiple series
    - Consistent styling
    """

    # Color palette
    COLORS = [
        '#1976d2',  # Blue
        '#4caf50',  # Green
        '#ff9800',  # Orange
        '#9c27b0',  # Purple
        '#f44336',  # Red
        '#00bcd4',  # Cyan
        '#ff5722',  # Deep Orange
        '#3f51b5',  # Indigo
    ]

    def __init__(self, figsize: Tuple[float, float] = (10, 6), dpi: int = 100):
        """
        Initialize chart builder.

        Args:
            figsize: Figure size in inches (width, height)
            dpi: Dots per inch for rendering
        """
        if not MATPLOTLIB_AVAILABLE:
            raise ImportError(
                "matplotlib not installed. "
                "Install it with: pip install matplotlib --break-system-packages"
            )

        self.figsize = figsize
        self.dpi = dpi
        self.figure: Optional[Figure] = None
        self.canvas: Optional[FigureCanvas] = None

    # ========================================================================
    # Line Charts
    # ========================================================================

    def create_line_chart(
        self,
        data: Dict[str, List],
        title: str,
        xlabel: str = "",
        ylabel: str = "",
        show_grid: bool = True,
        show_legend: bool = True
    ) -> FigureCanvas:
        """
        Create a line chart.

        Args:
            data: Dictionary mapping series name to list of values
                  Include 'x' key for x-axis values
            title: Chart title
            xlabel: X-axis label
            ylabel: Y-axis label
            show_grid: Show grid lines
            show_legend: Show legend

        Returns:
            FigureCanvas widget

        Example:
            data = {
                'x': ['Jan', 'Feb', 'Mar'],
                'Revenue': [10000, 12000, 15000],
                'Costs': [7000, 8000, 9000]
            }
        """
        self.figure = Figure(figsize=self.figsize, dpi=self.dpi)
        ax = self.figure.add_subplot(111)

        x_values = data.get('x', list(range(len(next(iter(data.values()))))))

        color_idx = 0
        for key, values in data.items():
            if key == 'x':
                continue

            ax.plot(
                x_values,
                values,
                marker='o',
                linewidth=2,
                markersize=6,
                label=key,
                color=self.COLORS[color_idx % len(self.COLORS)]
            )
            color_idx += 1

        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        if xlabel:
            ax.set_xlabel(xlabel, fontsize=11)
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=11)

        if show_grid:
            ax.grid(True, alpha=0.3, linestyle='--')

        if show_legend and len(data) > 2:  # More than just x and one series
            ax.legend(loc='best', framealpha=0.9)

        self.figure.tight_layout()

        self.canvas = FigureCanvas(self.figure)
        return self.canvas

    # ========================================================================
    # Bar Charts
    # ========================================================================

    def create_bar_chart(
        self,
        categories: List[str],
        values: List[float],
        title: str,
        xlabel: str = "",
        ylabel: str = "",
        color: Optional[str] = None,
        horizontal: bool = False
    ) -> FigureCanvas:
        """
        Create a bar chart.

        Args:
            categories: Category labels
            values: Values for each category
            title: Chart title
            xlabel: X-axis label
            ylabel: Y-axis label
            color: Bar color (default: blue)
            horizontal: Create horizontal bar chart

        Returns:
            FigureCanvas widget
        """
        self.figure = Figure(figsize=self.figsize, dpi=self.dpi)
        ax = self.figure.add_subplot(111)

        bar_color = color or self.COLORS[0]

        if horizontal:
            ax.barh(categories, values, color=bar_color, alpha=0.8)
            if ylabel:
                ax.set_xlabel(ylabel, fontsize=11)
            if xlabel:
                ax.set_ylabel(xlabel, fontsize=11)
        else:
            ax.bar(categories, values, color=bar_color, alpha=0.8)
            if xlabel:
                ax.set_xlabel(xlabel, fontsize=11)
            if ylabel:
                ax.set_ylabel(ylabel, fontsize=11)

            # Rotate x labels if too many
            if len(categories) > 5:
                ax.tick_params(axis='x', rotation=45)

        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        ax.grid(True, alpha=0.3, linestyle='--', axis='y' if not horizontal else 'x')

        self.figure.tight_layout()

        self.canvas = FigureCanvas(self.figure)
        return self.canvas

    def create_grouped_bar_chart(
        self,
        categories: List[str],
        data: Dict[str, List[float]],
        title: str,
        xlabel: str = "",
        ylabel: str = "",
        show_legend: bool = True
    ) -> FigureCanvas:
        """
        Create a grouped bar chart.

        Args:
            categories: Category labels
            data: Dictionary mapping series name to values
            title: Chart title
            xlabel: X-axis label
            ylabel: Y-axis label
            show_legend: Show legend

        Returns:
            FigureCanvas widget

        Example:
            categories = ['Q1', 'Q2', 'Q3']
            data = {
                'Revenue': [100, 120, 150],
                'Costs': [70, 80, 90]
            }
        """
        import numpy as np

        self.figure = Figure(figsize=self.figsize, dpi=self.dpi)
        ax = self.figure.add_subplot(111)

        x = np.arange(len(categories))
        width = 0.8 / len(data)

        for idx, (label, values) in enumerate(data.items()):
            offset = (idx - len(data) / 2) * width + width / 2
            ax.bar(
                x + offset,
                values,
                width,
                label=label,
                color=self.COLORS[idx % len(self.COLORS)],
                alpha=0.8
            )

        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        ax.set_xticks(x)
        ax.set_xticklabels(categories)

        if xlabel:
            ax.set_xlabel(xlabel, fontsize=11)
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=11)

        if show_legend:
            ax.legend(loc='best', framealpha=0.9)

        ax.grid(True, alpha=0.3, linestyle='--', axis='y')

        self.figure.tight_layout()

        self.canvas = FigureCanvas(self.figure)
        return self.canvas

    # ========================================================================
    # Pie Charts
    # ========================================================================

    def create_pie_chart(
        self,
        labels: List[str],
        values: List[float],
        title: str,
        show_percentages: bool = True,
        explode: Optional[List[float]] = None
    ) -> FigureCanvas:
        """
        Create a pie chart.

        Args:
            labels: Slice labels
            values: Values for each slice
            title: Chart title
            show_percentages: Show percentage labels
            explode: Optional list of explosion distances

        Returns:
            FigureCanvas widget
        """
        self.figure = Figure(figsize=(8, 8), dpi=self.dpi)
        ax = self.figure.add_subplot(111)

        colors = self.COLORS[:len(labels)]

        autopct = '%1.1f%%' if show_percentages else None

        ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct=autopct,
            startangle=90,
            explode=explode,
            shadow=True
        )

        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        ax.axis('equal')

        self.figure.tight_layout()

        self.canvas = FigureCanvas(self.figure)
        return self.canvas

    # ========================================================================
    # Specialized Charts
    # ========================================================================

    def create_trend_chart_with_growth(
        self,
        periods: List[str],
        values: List[float],
        title: str,
        ylabel: str = "Value"
    ) -> FigureCanvas:
        """
        Create trend chart with growth indicators.

        Args:
            periods: Period labels
            values: Values for each period
            title: Chart title
            ylabel: Y-axis label

        Returns:
            FigureCanvas widget
        """
        self.figure = Figure(figsize=self.figsize, dpi=self.dpi)
        ax = self.figure.add_subplot(111)

        # Main line
        ax.plot(
            periods,
            values,
            marker='o',
            linewidth=2.5,
            markersize=8,
            color=self.COLORS[0],
            label='Actual'
        )

        # Add trend line
        import numpy as np
        x_numeric = np.arange(len(periods))
        z = np.polyfit(x_numeric, values, 1)
        p = np.poly1d(z)
        ax.plot(
            periods,
            p(x_numeric),
            linestyle='--',
            linewidth=2,
            color=self.COLORS[3],
            alpha=0.7,
            label='Trend'
        )

        # Highlight growth/decline
        for i in range(1, len(values)):
            if values[i] > values[i-1]:
                color = self.COLORS[1]  # Green
            else:
                color = self.COLORS[4]  # Red

            ax.annotate(
                '',
                xy=(i, values[i]),
                xytext=(i-1, values[i-1]),
                arrowprops=dict(
                    arrowstyle='->',
                    color=color,
                    lw=1.5,
                    alpha=0.5
                )
            )

        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(loc='best', framealpha=0.9)

        if len(periods) > 5:
            ax.tick_params(axis='x', rotation=45)

        self.figure.tight_layout()

        self.canvas = FigureCanvas(self.figure)
        return self.canvas

    def create_comparison_chart(
        self,
        current_label: str,
        current_values: List[float],
        previous_label: str,
        previous_values: List[float],
        categories: List[str],
        title: str
    ) -> FigureCanvas:
        """
        Create a comparison chart (current vs previous period).

        Args:
            current_label: Label for current period
            current_values: Current period values
            previous_label: Label for previous period
            previous_values: Previous period values
            categories: Category labels
            title: Chart title

        Returns:
            FigureCanvas widget
        """
        import numpy as np

        self.figure = Figure(figsize=self.figsize, dpi=self.dpi)
        ax = self.figure.add_subplot(111)

        x = np.arange(len(categories))
        width = 0.35

        ax.bar(
            x - width/2,
            previous_values,
            width,
            label=previous_label,
            color=self.COLORS[5],
            alpha=0.8
        )

        ax.bar(
            x + width/2,
            current_values,
            width,
            label=current_label,
            color=self.COLORS[0],
            alpha=0.8
        )

        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        ax.set_xticks(x)
        ax.set_xticklabels(categories)
        ax.legend(loc='best', framealpha=0.9)
        ax.grid(True, alpha=0.3, linestyle='--', axis='y')

        if len(categories) > 5:
            ax.tick_params(axis='x', rotation=45)

        self.figure.tight_layout()

        self.canvas = FigureCanvas(self.figure)
        return self.canvas

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def save_chart(self, filename: str, dpi: int = 150):
        """
        Save current chart to file.

        Args:
            filename: Output filename
            dpi: Resolution (dots per inch)
        """
        if self.figure:
            self.figure.savefig(filename, dpi=dpi, bbox_inches='tight')

    def get_canvas(self) -> Optional[FigureCanvas]:
        """Get the current canvas widget."""
        return self.canvas

    def clear(self):
        """Clear current figure and canvas."""
        if self.figure:
            plt.close(self.figure)
        self.figure = None
        self.canvas = None
