(() => {
    const channels = ['Norte', 'Sur', 'Este', 'Oeste'];
    const values = ['valN', 'valS', 'valE', 'valO'].map(id => document.getElementById(id));
    const status = document.getElementById('connection-status');
    const chart = typeof Chart === 'function' ? new Chart(document.getElementById('chart'), {
        type: 'line',
        data: {
            labels: [],
            datasets: channels.map((name, index) => ({
                label: name + ' (A)', data: [],
                borderColor: ['red', 'blue', 'green', 'purple'][index],
                borderWidth: 1, yAxisID: index === 3 ? 'y2' : 'y'
            }))
        },
        options: {
            animation: false,
            responsive: true,
            scales: {
                x: { title: { display: true, text: 'Tiempo (s)' } },
                y: {
                    position: 'left', beginAtZero: false,
                    title: { display: true, text: 'Corriente (A)' }
                },
                y2: {
                    display: true, position: 'right', beginAtZero: false,
                    grid: { drawOnChartArea: false }
                }
            }
        }
    }) : null;

    createDevicePoller({
        async run(signal) {
            const response = await fetch('/api/history', { cache: 'no-store', signal });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            const series = channels.map(name => data[name]);
            if (!series.every(points => Array.isArray(points) && points.length <= 40
                && points.every(Number.isFinite) && points.length === series[0].length)) {
                throw new Error('Historial inválido');
            }
            if (signal.aborted) return;
            const last = series[0].length - 1;
            values.forEach((element, index) => {
                element.textContent = last >= 0 ? series[index][last].toFixed(2) : '—';
            });
            if (chart) {
                const dt = Number(data.SAMPLE_INTERVAL_MS || 500) / 1000;
                chart.data.labels = series[0].map((_, index) => ((index - last) * dt).toFixed(1) + 's');
                series.forEach((points, index) => { chart.data.datasets[index].data = points; });
                const allValues = [].concat(...series);
                if (allValues.length) {
                    const min = Math.min(...allValues);
                    const max = Math.max(...allValues);
                    const padding = (max - min) * 0.1 || 1;
                    for (const axis of ['y', 'y2']) {
                        chart.options.scales[axis].min = min - padding;
                        chart.options.scales[axis].max = max + padding;
                    }
                }
                chart.update('none');
            }
            if (data.calibration_ready === false) {
                status.textContent = data.warning;
                document.getElementById('calibration-panel').open = true;
                return;
            }
            status.textContent = chart ? 'Conectado al ESP32.'
                : 'Lecturas conectadas. Recarga la página para volver a cargar la gráfica.';
        },
        onError() {
            values.forEach(element => { element.textContent = '—'; });
            status.textContent = 'Sin datos nuevos del ESP32. Reconectando automáticamente…';
        }
    });
})();
