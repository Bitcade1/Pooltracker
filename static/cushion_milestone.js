(function () {
    'use strict';

    const overlay = document.getElementById('cushionSilicaCelebration');
    if (!overlay || Number(overlay.dataset.count) < 140) return;

    const storageKey = `cushion-silica-140|${overlay.dataset.period}`;
    try {
        if (window.localStorage.getItem(storageKey)) return;
    } catch (error) {
        // The celebration still works when browser storage is unavailable.
    }

    const canvas = overlay.querySelector('canvas');
    const context = canvas.getContext('2d');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const duration = 10000;
    const packetCount = 3000;
    let width;
    let height;
    let frameId;
    let stopTimer;
    let stopped = false;
    let emitted = 0;
    let previousTime = 0;
    const packets = [];

    // Draw each packet once, then reuse the sprites for thousands of particles.
    function packetSprite(ink) {
        const sprite = document.createElement('canvas');
        sprite.width = 100;
        sprite.height = 126;
        const pen = sprite.getContext('2d');
        pen.fillStyle = '#fffdf0';
        pen.fillRect(3, 3, 94, 120);
        pen.strokeStyle = '#c8c3ae';
        pen.lineWidth = 2;
        pen.strokeRect(3, 3, 94, 120);
        pen.strokeStyle = '#d6cfb7';
        for (let x = 8; x < 96; x += 5) {
            pen.beginPath();
            pen.moveTo(x, 4);
            pen.lineTo(x, 13);
            pen.moveTo(x, 113);
            pen.lineTo(x, 122);
            pen.stroke();
        }
        pen.strokeStyle = ink;
        pen.strokeRect(12, 19, 76, 88);
        pen.fillStyle = ink;
        pen.textAlign = 'center';
        pen.font = 'bold 20px sans-serif';
        pen.fillText('SILICA', 50, 43);
        pen.fillText('GEL', 50, 65);
        pen.font = 'bold 9px sans-serif';
        pen.fillText('DESICCANT', 50, 83);
        pen.fillText('DO NOT EAT', 50, 99);
        return sprite;
    }

    function resize() {
        width = window.innerWidth;
        height = window.innerHeight;
        const scale = Math.min(window.devicePixelRatio || 1, 1.5);
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        context.setTransform(scale, 0, 0, scale, 0, 0);
    }

    function stop() {
        if (stopped) return;
        stopped = true;
        window.cancelAnimationFrame(frameId);
        window.clearTimeout(stopTimer);
        window.removeEventListener('resize', resize);
        window.removeEventListener('pagehide', stop);
        document.removeEventListener('keydown', onKeydown);
        overlay.hidden = true;
        packets.length = 0;
        canvas.width = 0;
        canvas.height = 0;
    }

    function onKeydown(event) {
        if (event.key === 'Escape') stop();
    }

    overlay.querySelector('button').addEventListener('click', stop);
    document.addEventListener('keydown', onKeydown);
    window.addEventListener('pagehide', stop);
    overlay.hidden = false;

    if (context && !reducedMotion) {
        const sprites = [packetSprite('#1e3a8a'), packetSprite('#1e3a8a'), packetSprite('#b45309')];
        resize();
        window.addEventListener('resize', resize);
        const startedAt = performance.now();
        previousTime = startedAt;

        function frame(now) {
            if (stopped) return;
            const elapsed = now - startedAt;
            if (elapsed >= duration) {
                stop();
                return;
            }
            const delta = Math.min((now - previousTime) / 1000, 0.05);
            previousTime = now;
            // Emit every packet early enough to fall before the ten seconds end.
            const desired = Math.min(packetCount, 100 + Math.floor(elapsed / 7500 * 2900));
            while (emitted < desired) {
                packets.push({
                    x: Math.random() * (width + 160) - 80,
                    y: -160 * Math.random() - 60,
                    size: 18 + Math.random() * 28,
                    speed: (height + 250) / (1.2 + Math.random()),
                    drift: (Math.random() - 0.5) * 150,
                    angle: Math.random() * Math.PI * 2,
                    spin: (Math.random() - 0.5) * 5,
                    sprite: sprites[Math.floor(Math.random() * sprites.length)],
                });
                emitted += 1;
            }
            context.clearRect(0, 0, width, height);
            for (let index = packets.length - 1; index >= 0; index -= 1) {
                const packet = packets[index];
                packet.y += packet.speed * delta;
                packet.x += packet.drift * delta;
                packet.angle += packet.spin * delta;
                if (packet.y > height + 100) {
                    packets.splice(index, 1);
                    continue;
                }
                context.save();
                context.translate(packet.x, packet.y);
                context.rotate(packet.angle);
                context.drawImage(packet.sprite, -packet.size / 2, -packet.size * 0.63,
                    packet.size, packet.size * 1.26);
                context.restore();
            }
            frameId = window.requestAnimationFrame(frame);
        }
        frameId = window.requestAnimationFrame(frame);
    }

    stopTimer = window.setTimeout(stop, duration);
    try {
        window.localStorage.setItem(storageKey, 'shown');
    } catch (error) {
        // Storage restrictions should not interrupt the animation.
    }
})();
