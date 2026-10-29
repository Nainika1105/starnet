import React from 'react'
import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet'

const COLOR = {
  VERIFIED: '#1d5fd0',
  ASSIGNED: '#9a6400',
  RESOLVED: '#1d7a4c',
  CONFIRMED: '#1d7a4c',
  REOPENED: '#b3261e',
  REJECTED: '#8b8b8b',
  REPORTED: '#6c6c6c',
}

export default function IssueMap({ issues, selectedId, onSelect }) {
  const centre = issues.length
    ? [issues[0].lat, issues[0].lng]
    : [12.9719, 77.5937]

  return (
    <MapContainer center={centre} zoom={12} scrollWheelZoom>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {issues.map((issue) => (
        <CircleMarker
          key={issue.id}
          center={[issue.lat, issue.lng]}
          radius={issue.id === selectedId ? 11 : 5 + issue.priority * 8}
          pathOptions={{
            color: COLOR[issue.status] || '#6c6c6c',
            fillColor: COLOR[issue.status] || '#6c6c6c',
            fillOpacity: issue.id === selectedId ? 0.85 : 0.45,
            weight: issue.id === selectedId ? 3 : 1,
          }}
          eventHandlers={{ click: () => onSelect?.(issue.id) }}
        >
          <Popup>
            <strong>{issue.category}</strong>
            <br />
            {issue.status} · priority {issue.priority.toFixed(2)}
            <br />
            {issue.ward}
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}
