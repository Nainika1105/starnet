import React, { useRef, useState } from 'react'
import { Modal, Pressable, Text, View } from 'react-native'
import { CameraView, useCameraPermissions } from 'expo-camera'
import { theme } from '../theme'
import { Btn, s } from './ui'

/**
 * Live capture only -- there is deliberately no gallery picker anywhere in this
 * app. Letting someone upload a saved image is the easiest way to file a stale
 * or borrowed photo, and the geo/EXIF coherence check is much weaker against it.
 *
 * Note this is UI enforcement, not proof: a determined user can still feed the
 * camera a photograph of a screen. Hardware attestation is out of scope here.
 *
 * EXIF is requested on capture because the server's geo-coherence check reads
 * the capture time and GPS off the image itself -- a photo that arrives with no
 * EXIF scores as a re-encoded or borrowed one and may not clear the confidence
 * floor. The server keeps those two fields and strips everything else before
 * storing the image.
 */
export default function LiveCamera({ visible, onClose, onCapture, prompt }) {
  const [permission, requestPermission] = useCameraPermissions()
  const [busy, setBusy] = useState(false)
  const cameraRef = useRef(null)

  async function shoot() {
    if (!cameraRef.current || busy) return
    setBusy(true)
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.6, exif: true })
      onCapture(photo.uri)
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: '#000' }}>
        {!permission?.granted ? (
          <View style={{ flex: 1, justifyContent: 'center', padding: 24, gap: 12 }}>
            <Text style={[s.h2, { color: '#fff' }]}>Camera access needed</Text>
            <Text style={{ color: '#aaa', fontSize: 14, lineHeight: 20 }}>
              STAR-Net only accepts photos taken live, so a report cannot be filed with a
              stale or reused image. There is no gallery option.
            </Text>
            <Btn title="Allow camera" kind="primary" onPress={requestPermission} />
            <Btn title="Cancel" onPress={onClose} />
          </View>
        ) : (
          <>
            <CameraView ref={cameraRef} style={{ flex: 1 }} facing="back" />
            <View style={{ padding: 20, paddingBottom: 36, gap: 10, backgroundColor: '#000' }}>
              {prompt ? (
                <Text style={{ color: '#ddd', fontSize: 13, textAlign: 'center' }}>{prompt}</Text>
              ) : null}
              <Pressable
                onPress={shoot}
                disabled={busy}
                style={{
                  alignSelf: 'center',
                  width: 68,
                  height: 68,
                  borderRadius: 34,
                  backgroundColor: busy ? '#555' : '#fff',
                  borderWidth: 4,
                  borderColor: theme.accent,
                }}
              />
              <Btn title="Cancel" onPress={onClose} />
            </View>
          </>
        )}
      </View>
    </Modal>
  )
}
