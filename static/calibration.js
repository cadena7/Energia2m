(() => {
    const channels = ['Norte', 'Sur', 'Este', 'Oeste'];
    const panel = document.getElementById('calibration-panel');
    const form = document.getElementById('calibration-form');
    const fields = document.getElementById('calibration-fields');
    const status = document.getElementById('calibration-status');
    const live = document.getElementById('calibration-live');
    const rows = document.getElementById('calibration-rows');
    const inputs = {};
    const deadbandInputs = {};
    const voltages = {};
    let initialized = false;
    let dirty = false;
    let saving = false;
    let reloadOffsets = true;

    channels.forEach(channel => {
        const row = document.createElement('tr');
        const labelCell = document.createElement('th');
        labelCell.scope = 'row';
        const label = document.createElement('label');
        label.htmlFor = 'offset-' + channel;
        label.textContent = channel;
        labelCell.appendChild(label);
        const voltage = document.createElement('td');
        voltage.textContent = '—';
        const inputCell = document.createElement('td');
        const input = document.createElement('input');
        input.id = label.htmlFor;
        input.type = 'number';
        input.required = true;
        input.min = '0.0001';
        input.max = '3.3299';
        // Ajuste fino: las flechas cambian 0.0001 V por paso.
        input.step = '0.0001';
        input.inputMode = 'decimal';
        input.setAttribute('aria-label', 'Offset de ' + channel + ' en voltios');
        inputCell.appendChild(input);
        const deadbandCell = document.createElement('td');
        const deadband = document.createElement('input');
        deadband.id = 'deadband-' + channel;
        deadband.type = 'number';
        deadband.required = true;
        deadband.min = '0';
        deadband.max = '20';
        // Ajuste fino: las flechas cambian 0.01 A por paso.
        deadband.step = '0.01';
        deadband.inputMode = 'decimal';
        deadband.setAttribute('aria-label', 'Zona muerta de ' + channel + ' en amperes');
        deadbandCell.appendChild(deadband);
        row.append(labelCell, voltage, inputCell, deadbandCell);
        rows.appendChild(row);
        inputs[channel] = input;
        deadbandInputs[channel] = deadband;
        voltages[channel] = voltage;
    });

    function message(text, error = false) {
        status.textContent = text;
        status.classList.toggle('calibration-error', error);
    }

    function fillCalibration(data) {
        channels.forEach(channel => {
            inputs[channel].value = data.offsets[channel] == null ? '' : String(data.offsets[channel]);
            deadbandInputs[channel].value = data.deadbands[channel] == null ? '' : String(data.deadbands[channel]);
        });
    }

    async function request(options = {}) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch('/api/calibration', {
                ...options, cache: 'no-store', signal: controller.signal
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Error de comunicación con el ESP32.');
            return data;
        } finally {
            clearTimeout(timeout);
        }
    }

    const poller = createDevicePoller({
        enabled: () => panel.open && !saving,
        async run(signal) {
            const response = await fetch('/api/calibration', { cache: 'no-store', signal });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            if (saving || signal.aborted) return;
            if (!initialized || (reloadOffsets && !dirty)) {
                fillCalibration(data);
                initialized = true;
                fields.disabled = false;
                message(data.warning || 'Calibración activa cargada. Los cambios se conservan al apagar el equipo.', data.ready === false);
            }
            reloadOffsets = false;
            channels.forEach(channel => {
                const value = data.voltages[channel];
                voltages[channel].textContent = Number.isFinite(value) ? value.toFixed(4) : '—';
            });
            live.textContent = 'Voltajes actualizados: ' + new Date().toLocaleTimeString();
        },
        onError() {
            channels.forEach(channel => { voltages[channel].textContent = '—'; });
            live.textContent = 'Sin lectura de voltajes: no se pudo consultar el ESP32.';
            if (!initialized) message('No se pudo cargar la configuración. Reintentando…', true);
        }
    });

    form.addEventListener('input', () => {
        dirty = true;
        message('Hay cambios sin guardar.');
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!initialized || saving || !form.reportValidity()) return;
        const offsets = {};
        const deadbands = {};
        channels.forEach(channel => {
            offsets[channel] = Number(inputs[channel].value);
            deadbands[channel] = Number(deadbandInputs[channel].value);
        });
        saving = true;
        poller.refresh();  // Cancel the read before sending a configuration write.
        fields.disabled = true;
        message('Guardando y aplicando…');
        try {
            const data = await request({
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ offsets, deadbands })
            });
            fillCalibration(data);
            dirty = false;
            message(data.message);
        } catch (error) {
            message(error.name === 'AbortError' || error instanceof TypeError
                ? 'No se pudo confirmar el guardado. Conservamos tus campos; vuelve a guardar para confirmar.'
                : error.message, true);
        } finally {
            saving = false;
            fields.disabled = false;
            poller.refresh();
        }
    });

    panel.addEventListener('toggle', () => {
        reloadOffsets = true;
        poller.refresh();
    });
})();
