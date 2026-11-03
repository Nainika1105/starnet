import React, { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Live capture for the browser.
 *
 * There is deliberately no file picker here, exactly as there is none in the
 * mobile app. Letting someone attach a saved image is the easiest way to file a
 * stale or borrowed photo, and the whole point of the capture rule is that the
 * reporter was standing there.
 *
 * Every button here sets type="button" explicitly. This component renders
 * inside the report <form>, where a button with no type defaults to submit --
 * which files the report with no photo and gets it rejected for lack of
 * evidence, with nothing on screen to explain why.
 *
 * Two things are honestly different from the phone:
 *
 *   1. A browser capture carries no EXIF. The server reads capture time and GPS
 *      off the image itself, and a canvas frame has neither, so a browser
 *      report scores lower on geo coherence than the same report from the app.
 *      That is not worked around here -- a client-supplied "trust me, this is
 *      live" field would be forgeable and would hand free credibility to
 *      whoever lied. The report is simply weaker evidence, which the confidence
 *      model is built to express.
 *
 *   2. When no camera is available, the fallback DRAWS an image rather than
 *      letting one be chosen. It cannot be a stale photograph of anything,
 *      because it is not a photograph. It is labelled as a stand-in wherever it
 *      appears.
 */
export default function Capture({ onCapture, captured, label = 'Photograph the fault' }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [state, setState] = useState('idle') // idle | starting | live | unavailable
  const [reason, setReason] = useState(null)

  const stop = useCallback(() => {
    const stream = streamRef.current
    if (stream) {
      stream.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
  }, [])

  useEffect(() => stop, [stop])

  async function start() {
    setState('starting')
    setReason(null)
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('This browser does not offer camera access.')
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      })
      streamRef.current = stream
      setState('live')
      // The element exists only once state is 'live', so attach on the next tick.
      requestAnimationFrame(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play().catch(() => {})
        }
      })
    } catch (err) {
      setState('unavailable')
      setReason(
        err?.name === 'NotAllowedError'
          ? 'Camera access was declined.'
          : err?.message || 'No camera available.',
      )
    }
  }

  function shoot() {
    const video = videoRef.current
    if (!video || !video.videoWidth) return

    const canvas = document.createElement('canvas')
    canvas.width = Math.min(1280, video.videoWidth)
    canvas.height = Math.round((canvas.width / video.videoWidth) * video.videoHeight)
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)

    canvas.toBlob(
      (blob) => {
        if (!blob) return
        stop()
        setState('idle')
        onCapture({ blob, url: URL.createObjectURL(blob), synthetic: false })
      },
      'image/jpeg',
      0.8,
    )
  }

  function standIn() {
    // Drawn, not chosen. Textured so that two stand-ins are not perceptually
    // identical -- the server hashes every photo and discounts one it has seen
    // before, which it would be right to do if these were all the same bytes.
    const canvas = document.createElement('canvas')
    canvas.width = 640
    canvas.height = 480
    const ctx = canvas.getContext('2d')

    for (let y = 0; y < 480; y += 10) {
      for (let x = 0; x < 640; x += 10) {
        const v = 70 + Math.floor(Math.random() * 90)
        ctx.fillStyle = `rgb(${v},${v + 6},${v + 14})`
        ctx.fillRect(x, y, 10, 10)
      }
    }
    ctx.filter = 'blur(3px)'
    ctx.drawImage(canvas, 0, 0)
    ctx.filter = 'none'

    ctx.fillStyle = 'rgba(22,24,28,0.92)'
    ctx.beginPath()
    ctx.ellipse(320, 280, 150, 95, 0, 0, Math.PI * 2)
    ctx.fill()

    ctx.strokeStyle = 'rgba(255,255,255,0.85)'
    ctx.lineWidth = 3
    ctx.strokeRect(16, 16, 608, 448)
    ctx.fillStyle = 'rgba(255,255,255,0.9)'
    ctx.font = '600 20px system-ui, sans-serif'
    ctx.fillText('STAND-IN IMAGE — not a photograph', 34, 54)

    canvas.toBlob(
      (blob) => {
        if (!blob) return
        onCapture({ blob, url: URL.createObjectURL(blob), synthetic: true })
      },
      'image/jpeg',
      0.8,
    )
  }

  if (captured) {
    return (
      <div className="capture">
        <img src={captured.url} alt="The photo about to be submitted" className="capture-shot" />
        <div className="row">
          {captured.synthetic ? (
            <span className="tag warn">Stand-in image, drawn in the browser</span>
          ) : (
            <span className="tag ok">Captured live</span>
          )}
          <button
            type="button"
            className="btn"
            onClick={() => {
              URL.revokeObjectURL(captured.url)
              onCapture(null)
            }}
          >
            Retake
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="capture">
      {state === 'live' ? (
        <>
          <video ref={videoRef} className="capture-shot" playsInline muted />
          <div className="row">
            <button type="button" className="btn primary" onClick={shoot}>
              Capture
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                stop()
                setState('idle')
              }}
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="capture-empty">
            <strong>{label}</strong>
            <p className="muted small">
              Live capture only. There is no option to attach a saved image, here or in the
              mobile app — a photo chosen from a library is the cheapest way to file a fault
              that is not there.
            </p>
          </div>
          <div className="row">
            <button
              type="button"
              className="btn primary"
              onClick={start}
              disabled={state === 'starting'}
            >
              {state === 'starting' ? 'Opening camera…' : 'Open camera'}
            </button>
            <button type="button" className="btn" onClick={standIn}>
              Use a stand-in image
            </button>
          </div>
          {state === 'unavailable' && (
            <p className="small warn-text">
              {reason} You can still walk through the flow with a stand-in image, which is drawn
              here and then — rather than chosen, so it cannot be a stale photograph of anything.
            </p>
          )}
          <p className="small muted">
            A browser capture carries no EXIF, so the server's geo and timestamp checks have less
            to read than they would from the phone app, and the report scores lower because of it.
            That is left as it is rather than papered over with a field the client could fake.
          </p>
        </>
      )}
    </div>
  )
}
