export function DocsPage() {
  const sections = [
    {
      title: 'Supported commands',
      items: [
        { cmd: 'Install VS Code',                desc: 'Install via winget / direct download' },
        { cmd: 'Download Python 3.12 for Windows', desc: 'Download only, skip install' },
        { cmd: 'Install Docker Desktop',           desc: 'Full install with verification' },
        { cmd: 'Install Postman',                  desc: 'winget on Windows, brew on macOS' },
        { cmd: 'VS Code install karo',             desc: 'Hinglish — same as above' },
        { cmd: 'Python download chahiye',          desc: 'Hindi / Hinglish intent' },
      ],
    },
    {
      title: 'Supported software (25+)',
      items: [
        { cmd: 'VS Code, Sublime Text, Notepad++', desc: 'Code editors' },
        { cmd: 'Python, Node.js, Go, Rust, Java',  desc: 'Runtimes' },
        { cmd: 'Git, Docker, Postman',             desc: 'Dev tools' },
        { cmd: 'Chrome, Firefox',                  desc: 'Browsers' },
        { cmd: 'Slack, Zoom, Discord, Telegram',   desc: 'Communication' },
        { cmd: 'GIMP, Inkscape, Blender, VLC',     desc: 'Creative / media' },
        { cmd: 'IntelliJ IDEA, PyCharm, Android Studio', desc: 'IDEs' },
        { cmd: '7-Zip, OBS Studio',                desc: 'Utilities' },
      ],
    },
    {
      title: 'Installation methods',
      items: [
        { cmd: 'winget',       desc: 'Windows Package Manager (preferred on Windows)' },
        { cmd: 'brew',         desc: 'Homebrew (preferred on macOS)' },
        { cmd: 'apt / snap',   desc: 'Linux package managers' },
        { cmd: 'Direct .exe',  desc: 'Silent NSIS/InnoSetup installer fallback' },
        { cmd: 'Direct .msi',  desc: 'msiexec /qn silent fallback' },
        { cmd: '.dmg / .pkg',  desc: 'macOS disk image / package fallback' },
      ],
    },
    {
      title: 'Security features',
      items: [
        { cmd: 'HTTPS-only downloads', desc: 'HTTP downloads are rejected' },
        { cmd: 'Trusted domain list',  desc: '30+ official publisher domains allowlisted' },
        { cmd: 'SHA-256 checksum',     desc: 'File integrity verified post-download' },
        { cmd: 'Authenticode (Win)',   desc: 'Publisher signature verified on Windows' },
        { cmd: 'codesign (Mac)',        desc: 'Apple code signature verified on macOS' },
      ],
    },
  ]

  return (
    <div className="space-y-6">
      {sections.map(section => (
        <div key={section.title} className="card overflow-hidden">
          <div className="px-5 py-3 border-b border-surface-600 bg-surface-700/50">
            <h2 className="text-sm font-medium text-slate-300">{section.title}</h2>
          </div>
          <div className="divide-y divide-surface-600">
            {section.items.map(item => (
              <div key={item.cmd} className="px-5 py-3 flex items-start gap-4">
                <code className="text-xs bg-surface-700 text-brand-300 px-2 py-1 rounded
                                 font-mono whitespace-nowrap flex-shrink-0">
                  {item.cmd}
                </code>
                <p className="text-sm text-slate-400">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
