class GaugeChart {
    constructor(canvasId, options) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.options = {
            min: options.min || 0,
            max: options.max || 100,
            color: options.color || '#4CAF50',
            fadeColor: options.fadeColor || 'rgba(76, 175, 80, 0.2)'
        };
        this.value = 0;
        this.init();
    }

    init() {
        this.chart = new Chart(this.ctx, {
            type: 'doughnut',
            data: {
                datasets: [{
                    data: [this.value, this.options.max - this.value],
                    backgroundColor: [this.options.color, this.options.fadeColor],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                cutout: '70%',
                rotation: -90,
                circumference: 180,
                plugins: { legend: { display: false }, tooltip: { enabled: false } }
            }
        });
    }

    update(newValue) {
        if (isNaN(newValue)) return;
        this.value = Math.max(this.options.min, Math.min(this.options.max, newValue));
        const ratio = (this.value - this.options.min) / (this.options.max - this.options.min || 1);
        const alpha = 0.3 + (ratio * 0.7);
        const rgbMatch = this.options.color.match(/\d+/g);
        const dynamicColor = rgbMatch ? `rgba(${rgbMatch[0]}, ${rgbMatch[1]}, ${rgbMatch[2]}, ${alpha})` : this.options.color;
        this.chart.data.datasets[0].data = [this.value, this.options.max - this.value];
        this.chart.data.datasets[0].backgroundColor[0] = dynamicColor;
        this.chart.update('none');
    }

    setOffline() {
        this.chart.data.datasets[0].backgroundColor[0] = 'rgba(120,120,120,0.35)';
        this.chart.update('none');
    }
}

/* ── Storage chamber gauges (original colours) ─────────────────────────────── */

class TemperatureGauge extends GaugeChart {
    constructor(canvasId) {
        super(canvasId, {
            min: 0, max: 40,
            color: 'rgb(76, 175, 80)',
            fadeColor: 'rgba(76, 175, 80, 0.2)'
        });
    }

    update(newValue) {
        if (isNaN(newValue)) return;
        let color;
        if (newValue < 10) color = 'rgb(33, 150, 243)';
        else if (newValue < 25) color = 'rgb(76, 175, 80)';
        else if (newValue < 35) color = 'rgb(255, 152, 0)';
        else color = 'rgb(244, 67, 54)';
        this.options.color = color;
        super.update(newValue);
    }
}

class HumidityGauge extends GaugeChart {
    constructor(canvasId) {
        super(canvasId, {
            min: 0, max: 100,
            color: 'rgb(33, 150, 243)',
            fadeColor: 'rgba(33, 150, 243, 0.2)'
        });
    }
}

/* ── Sensor chamber gauges (purple / cyan palette) ──────────────────────────── */

class SensorChamberTempGauge extends GaugeChart {
    constructor(canvasId) {
        super(canvasId, {
            min: 0, max: 50,
            color: 'rgb(156, 91, 204)',
            fadeColor: 'rgba(156, 91, 204, 0.2)'
        });
    }

    update(newValue) {
        if (isNaN(newValue)) return;
        // Higher temps in the sensor chamber are normal — use purple → amber range
        let color;
        if (newValue < 20) color = 'rgb(156, 91, 204)';      // cool purple
        else if (newValue < 35) color = 'rgb(171, 71, 188)';  // warm purple
        else color = 'rgb(255, 152, 0)';                       // amber warning
        this.options.color = color;
        super.update(newValue);
    }
}

class SensorChamberHumidityGauge extends GaugeChart {
    constructor(canvasId) {
        super(canvasId, {
            min: 0, max: 100,
            color: 'rgb(0, 188, 212)',
            fadeColor: 'rgba(0, 188, 212, 0.2)'
        });
    }
}
