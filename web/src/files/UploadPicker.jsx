// File selection is the keyboard-accessible entrance to the drop staging
// path. Retiring a project stops its remaining batch and late UI updates.
import {useEffect, useRef, useState} from 'react';
import {stageOne} from '../workbench/stageUpload.js';

export default function UploadPicker() {
  const input = useRef(null);
  const live = useRef(true);
  const pending = useRef(false);
  const [working, setWorking] = useState(false);
  const [failures, setFailures] = useState([]);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  const select = async event => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (pending.current || !files.length) return;
    pending.current = true;
    setWorking(true);
    setFailures([]);
    const failed = [];
    for (const file of files) {
      if (!live.current) break;
      try {
        await stageOne(file, () => live.current);
      } catch (error) {
        failed.push(`${file.name}: ${error?.message ?? error}`);
      }
    }
    if (live.current) {
      pending.current = false;
      setWorking(false);
      setFailures(failed);
    }
  };
  return (
    <div className="wb-upload-picker">
      <button type="button" className="button" disabled={working} onClick={() => input.current?.click()}>
        {working ? 'Uploading…' : 'Upload…'}
      </button>
      <input ref={input} type="file" hidden multiple aria-label="Upload files" onChange={select} />
      {failures.map(line => <p key={line} role="alert" className="panel__error">{line}</p>)}
    </div>
  );
}
