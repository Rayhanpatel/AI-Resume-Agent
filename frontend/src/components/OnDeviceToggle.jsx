import { memo } from 'react'
import { Laptop, Cloud, Download } from 'lucide-react'

/**
 * OnDeviceToggle
 *
 * Shown only when Chrome's built-in model is available on this machine.
 * States: off (cloud) → downloading (one-time) → on (answers stay on the device).
 */
function OnDeviceToggle({ device, onEnable, onDisable }) {
    if (!device || device.availability === 'unavailable' || device.availability === 'unknown') return null

    if (device.status === 'downloading') {
        const pct = Math.round((device.progress || 0) * 100)
        return (
            <span className="hidden sm:inline-flex items-center gap-2 text-xs text-gray-300 px-2 py-1 rounded-lg bg-white/5" title="Downloading the on-device model once">
                <Download className="w-3.5 h-3.5 animate-pulse" /> on-device model {pct}%
            </span>
        )
    }

    if (device.status === 'ready') {
        return (
            <button
                type="button"
                onClick={onDisable}
                className="hidden sm:inline-flex items-center gap-1.5 text-xs text-emerald-300 px-2 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/30 hover:bg-emerald-500/20 transition-colors"
                title="Answers are generated on this computer. Click to switch back to the cloud."
                aria-pressed="true"
            >
                <Laptop className="w-3.5 h-3.5" /> on device
            </button>
        )
    }

    return (
        <button
            type="button"
            onClick={onEnable}
            className="hidden sm:inline-flex items-center gap-1.5 text-xs text-gray-300 px-2 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 transition-colors"
            title={device.availability === 'available'
                ? 'Answer on this computer with Chrome\'s built-in model; your questions never leave it.'
                : 'Chrome can download its built-in model (a few GB, once) and answer on this computer.'}
            aria-pressed="false"
        >
            <Cloud className="w-3.5 h-3.5" /> try on-device
        </button>
    )
}

export default memo(OnDeviceToggle)
