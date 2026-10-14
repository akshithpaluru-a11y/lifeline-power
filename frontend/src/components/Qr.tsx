import { useEffect, useState } from 'react'
import QRCode from 'qrcode'

export function Qr({ value, size = 168 }: { value: string; size?: number }) {
  const [svg, setSvg] = useState('')
  useEffect(() => {
    QRCode.toString(value, { type: 'svg', margin: 0, errorCorrectionLevel: 'M', color: { dark: '#08090A', light: '#00000000' } }).then(setSvg)
  }, [value])
  return <div className="[&>svg]:h-full [&>svg]:w-full" style={{ width: size, height: size }} dangerouslySetInnerHTML={{ __html: svg }} />
}
