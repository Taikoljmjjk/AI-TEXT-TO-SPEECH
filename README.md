# AI-TEXT-TO-SPEECH
Chuyển văn bản thành giọng nói

import React, { useState, useRef, useEffect } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';

// Add type definitions for the File System Access API to avoid TypeScript errors.
declare global {
  interface Window {
    showSaveFilePicker(options?: {
      suggestedName?: string;
      types?: {
        description: string;
        accept: Record<string, string[]>;
      }[];
    }): Promise<{
      createWritable(): Promise<{
        write(data: Blob): Promise<void>;
        close(): Promise<void>;
      }>;
    }>;
  }
}

interface Voice {
  voice_id: string;
  name: string;
  labels?: Record<string, string>;
  description?: string;
  preview_url?: string;
  category?: string;
}

interface ProgressItem {
  line: string;
  status: 'pending' | 'processing' | 'done' | 'error';
}

interface UserInfo {
  subscription: {
    tier: string;
    character_limit: number;
    character_count: number;
  };
}

interface AppConfig {
  useProxy: boolean;
  proxyList: string;
  modelId: string;
  voiceId?: string;
  voiceSpeed: number;
  stability: number;
  similarity: number;
  addDelay: boolean;
  delay: number;
  splitDelimiters: string;
}


const App = () => {
  // Left Panel State
  const [apiKey, setApiKey] = useState('');
  const [apiKeyFileName, setApiKeyFileName] = useState('');
  const [apiKeysFromFile, setApiKeysFromFile] = useState<string[]>([]);
  const [useProxy, setUseProxy] = useState(false);
  const [proxyList, setProxyList] = useState('http://\nkhjtiNj3Kd:fdkm3nbjg45d@123.31.58.24:328085\nhttp://\nkhjtiNj3Kd:fdkm3nbjg45d@14.245.253.3:121288');
  const [voiceId, setVoiceId] = useState('');
  const [availableVoices, setAvailableVoices] = useState<Voice[]>([]);
  const [modelId, setModelId] = useState('eleven_multilingual_v2');
  const [voiceSpeed, setVoiceSpeed] = useState(1.00);
  const [stability, setStability] = useState(50);
  const [similarity, setSimilarity] = useState(75);
  const [addDelay, setAddDelay] = useState(false);
  const [delay, setDelay] = useState(0.00);

  // Right Panel State
  const [directText, setDirectText] = useState('');
  const [srtFilePath, setSrtFilePath] = useState('');
  const [subtitles, setSubtitles] = useState('');
  const [splitDelimiters, setSplitDelimiters] = useState('.,:;!?');


  // App Logic State
  const [isLoading, setIsLoading] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [statusMessage, setStatusMessage] = useState('Ready. Load API keys or enter one manually.');
  const [error, setError] = useState<string | null>(null);
  const [isFetchingVoices, setIsFetchingVoices] = useState(false);
  const [finalAudioUrl, setFinalAudioUrl] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressItem[]>([]);
  const [conversionProgress, setConversionProgress] = useState(0);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [totalRemainingChars, setTotalRemainingChars] = useState<number | null>(null);
  const [isCheckingApiKeys, setIsCheckingApiKeys] = useState(false);


  // Voice Find State
  const [voiceSearchInput, setVoiceSearchInput] = useState('');
  const [foundVoiceInfo, setFoundVoiceInfo] = useState<Voice | null>(null);
  const [isInfoModalOpen, setIsInfoModalOpen] = useState(false);
  const [isFindingVoice, setIsFindingVoice] = useState(false);


  const apiKeyFileInputRef = useRef<HTMLInputElement>(null);
  const srtFileInputRef = useRef<HTMLInputElement>(null);
  const currentKeyIndexRef = useRef(0);
  const CONFIG_KEY = 'ai-voice-app-config';
  
  // Cleanup object URL when component unmounts or URL changes
  useEffect(() => {
    return () => {
      if (finalAudioUrl) {
        URL.revokeObjectURL(finalAudioUrl);
      }
    };
  }, [finalAudioUrl]);

  // Effect to fetch user info when API key changes
  useEffect(() => {
    const fetchUserInfo = async (key: string) => {
        if (!key) {
            setUserInfo(null);
            return;
        }

        // When the user interacts with the single key input, we assume they are
        // no longer using the file-based keys. Clear file-related state.
        setApiKeysFromFile([]);
        setApiKeyFileName('');
        setTotalRemainingChars(null);

        try {
            const response = await fetch('https://api.elevenlabs.io/v1/user', {
                headers: { 'xi-api-key': key }
            });
            if (response.ok) {
                const data = await response.json();
                setUserInfo(data);
            } else {
                setUserInfo(null);
            }
        } catch (err) {
            setUserInfo(null);
            console.error('Error fetching user info:', err);
        }
    };
    
    // Debounce the fetch to avoid sending requests on every keystroke
    const timer = setTimeout(() => {
        fetchUserInfo(apiKey);
    }, 500);

    return () => clearTimeout(timer);

  }, [apiKey]);


  // Effect to calculate overall progress percentage
  useEffect(() => {
    if (progress.length === 0) {
      setConversionProgress(0);
      return;
    }
    const doneCount = progress.filter(p => p.status === 'done').length;
    const totalCount = progress.length;
    const percentage = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;
    setConversionProgress(percentage);
  }, [progress]);

  // Auto-load config on startup
  useEffect(() => {
    handleLoadConfig(true); // silent = true
  }, []);


  const getVoiceDisplayName = (voice: Voice) => {
    let displayName = voice.name;
    const accent = voice.labels?.accent;
    if (accent) {
        const capitalizedAccent = accent.charAt(0).toUpperCase() + accent.slice(1);
        displayName += ` (${capitalizedAccent})`;
    }
    displayName += ` (Voice ID: ${voice.voice_id})`;
    return displayName;
  };

  const handleSaveConfig = () => {
    const config: AppConfig = {
        useProxy,
        proxyList,
        modelId,
        voiceId,
        voiceSpeed,
        stability,
        similarity,
        addDelay,
        delay,
        splitDelimiters,
    };
    try {
        localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
        setStatusMessage('Cấu hình đã được lưu thành công.');
        setError(null);
    } catch (e) {
        setError('Không thể lưu cấu hình. Bộ nhớ có thể đã đầy.');
        setStatusMessage('Lỗi khi lưu cấu hình.');
    }
  };

  const handleLoadConfig = (silent = false) => {
      try {
          const savedConfig = localStorage.getItem(CONFIG_KEY);
          if (savedConfig) {
              const config: AppConfig = JSON.parse(savedConfig);
              
              setUseProxy(config.useProxy ?? false);
              setProxyList(config.proxyList ?? '');
              setModelId(config.modelId ?? 'eleven_multilingual_v2');
              if (config.voiceId) {
                  setVoiceId(config.voiceId);
              }
              setVoiceSpeed(config.voiceSpeed ?? 1.0);
              setStability(config.stability ?? 50);
              setSimilarity(config.similarity ?? 75);
              setAddDelay(config.addDelay ?? false);
              setDelay(config.delay ?? 0);
              setSplitDelimiters(config.splitDelimiters ?? '.,:;!?');
              
              if (!silent) {
                  setStatusMessage('Cấu hình đã được tải thành công.');
                  setError(null);
              }
          } else if (!silent) {
              setStatusMessage('Không tìm thấy cấu hình đã lưu.');
          }
      } catch (e) {
          if (!silent) {
              setError('Không thể tải cấu hình. Dữ liệu có thể bị hỏng.');
              setStatusMessage('Lỗi khi tải cấu hình.');
          }
      }
  };

  const handleDeleteConfig = () => {
      localStorage.removeItem(CONFIG_KEY);
      setStatusMessage('Cấu hình đã được xóa.');
      setError(null);
  };


  const handleCheckApiKey = async (key: string) => {
    if (!key) return;
    setIsFetchingVoices(true);
    setStatusMessage('Loading voices...');
    setError(null);
    try {
      const response = await fetch('https://api.elevenlabs.io/v1/voices', {
        headers: { 'xi-api-key': key }
      });
      if (!response.ok) {
        throw new Error('Failed to load voices. Check API Key.');
      }
      const data = await response.json();
      const sortedVoices = (data.voices as Voice[]).sort((a: Voice, b: Voice) => a.name.localeCompare(b.name));
      setAvailableVoices(sortedVoices);
      if (sortedVoices.length > 0 && !voiceId) {
        setVoiceId(sortedVoices[0].voice_id);
      }
      setStatusMessage('Voices loaded successfully.');
    } catch (err) {
       setError((err as Error).message);
       setStatusMessage('Error loading voices.');
    } finally {
      setIsFetchingVoices(false);
    }
  };
  
  const handleFindVoice = async (sourceInput: string) => {
    const keyToUse = apiKey || apiKeysFromFile[0];
    if (!keyToUse) {
      setError('API Key is required to find a voice.');
      return;
    }

    const trimmedInput = sourceInput.trim();
    if (!trimmedInput) {
      setError('Please enter a Voice ID or URL to search.');
      return;
    }

    // Intercept links to the general voice library and load all voices instead.
    if (trimmedInput.includes('voice-library')) {
      setStatusMessage('Library URL detected. Loading all available voices...');
      await handleCheckApiKey(keyToUse);
      return;
    }

    let voiceIdToFind = trimmedInput;
    try {
      const url = new URL(trimmedInput);
      const pathParts = url.pathname.split('/').filter(Boolean);
      if (pathParts.length > 0) {
        voiceIdToFind = pathParts[pathParts.length - 1];
      }
    } catch (e) {
      // Not a valid URL, so we'll just use the input as is.
    }

    setIsFindingVoice(true);
    setError(null);
    setFoundVoiceInfo(null);
    try {
      const response = await fetch(`https://api.elevenlabs.io/v1/voices/${voiceIdToFind}`, {
        headers: { 'xi-api-key': keyToUse }
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        // Use a more specific error message from the API if available.
        const detail = errorData.detail;
        let message = `Voice with ID '${voiceIdToFind}' not found or invalid API key.`;
        if (detail && typeof detail === 'string') {
          message = detail;
        } else if (detail && detail.message) {
          message = detail.message;
        }
        throw new Error(message);
      }

      const data: Voice = await response.json();

      // Set this as the currently selected voice
      setVoiceId(data.voice_id);

      // Add the voice to the dropdown list if it's not already there
      setAvailableVoices(prevVoices => {
        const voiceExists = prevVoices.some(v => v.voice_id === data.voice_id);
        if (voiceExists) {
          return prevVoices; // Don't add duplicates
        }
        const updatedVoices = [...prevVoices, data];
        updatedVoices.sort((a, b) => a.name.localeCompare(b.name));
        return updatedVoices;
      });

      // Show the details in the modal
      setFoundVoiceInfo(data);
      setIsInfoModalOpen(true);
      setStatusMessage(`Voice "${data.name}" found and selected.`);

    } catch (err) {
      setError((err as Error).message);
      setStatusMessage('Failed to find voice.');
    } finally {
      setIsFindingVoice(false);
    }
  };

  const handleVoiceSearchPaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const pastedText = e.clipboardData.getData('text');
    setVoiceSearchInput(pastedText);
    handleFindVoice(pastedText);
  };

  const checkAllApiKeys = async (keys: string[]) => {
      setIsCheckingApiKeys(true);
      setStatusMessage(`Checking ${keys.length} API keys...`);
      let totalChars = 0;
      let validKeysCount = 0;
  
      const promises = keys.map(key => 
          fetch('https://api.elevenlabs.io/v1/user', {
              headers: { 'xi-api-key': key }
          }).then(async res => {
              if (res.ok) {
                  return res.json();
              }
              return Promise.reject(); // Invalid key or other error
          }).catch(() => null)
      );
  
      const results = await Promise.all(promises);
  
      results.forEach(data => {
          if (data && data.subscription) {
              totalChars += (data.subscription.character_limit - data.subscription.character_count);
              validKeysCount++;
          }
      });
  
      setTotalRemainingChars(totalChars);
      setStatusMessage(`Found ${validKeysCount} valid keys. Total remaining characters: ${totalChars.toLocaleString('vi-VN')}`);
      setIsCheckingApiKeys(false);
  }

  const handleApiKeyFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      // Enter file mode: clear single key input and its info
      setApiKey('');
      setUserInfo(null);

      const reader = new FileReader();
      reader.onload = (event) => {
        const text = event.target?.result as string;
        const keys = text.split('\n').map(k => k.trim()).filter(Boolean);
        setApiKeysFromFile(keys);
        setApiKeyFileName(file.name);
        setTotalRemainingChars(null);
        if (keys.length > 0) {
            checkAllApiKeys(keys);
        } else {
            setStatusMessage('No API keys found in the file.');
        }
      };
      reader.readAsText(file);
    }
  };

  const handleSrtFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (event) => {
        const text = event.target?.result as string;
        setDirectText(text);
        setSubtitles(text);
        setSrtFilePath(file.name);
        setProgress([]); // Reset progress when new file is loaded
      };
      reader.readAsText(file);
    }
  };
  
  const handlePause = () => {
      setIsPaused(true);
      // Status message will be updated by the handleSynthesize loop when it detects the pause
  };

  const handleSplitText = () => {
    if (!directText.trim()) return;
  
    // Escape special regex characters from user input
    const escapedDelimiters = splitDelimiters.replace(/[\-\[\]\/\{\}\(\)\*\+\?\.\\\^\$\|]/g, "\\$&");
    if (!escapedDelimiters) return;
  
    // Create a regex to find all specified delimiters and capture them
    const regex = new RegExp(`([${escapedDelimiters}])`, 'g');
  
    // Replace each delimiter with itself followed by a newline character
    const textWithNewlines = directText.replace(regex, '$1\n');
  
    // Split by the newly inserted newlines, trim each line, filter out empty ones, and rejoin
    const newText = textWithNewlines
      .split('\n')
      .map(line => line.trim())
      .filter(line => line.length > 0)
      .join('\n');
  
    setDirectText(newText);
    setStatusMessage('Đã tách đoạn văn bản.');
  };

  const handleSynthesize = async () => {
    const textToConvert = directText;
    if (!textToConvert.trim()) {
      setError('Please provide text to convert.');
      return;
    }
    
    const keysToUse = apiKeysFromFile.length > 0 ? apiKeysFromFile : (apiKey ? [apiKey] : []);
    if (keysToUse.length === 0) {
      setError('Please provide at least one API Key from a file or the input field.');
      return;
    }

    if (!voiceId) {
      setError('Please select a voice.');
      return;
    }

    // Reset state for new conversion
    setIsLoading(true);
    setIsPaused(false);
    setError(null);
    setProgress([]);
    if (finalAudioUrl) {
      URL.revokeObjectURL(finalAudioUrl);
    }
    setFinalAudioUrl(null);
    currentKeyIndexRef.current = 0; // Always start with the first key

    const audioBlobs: Blob[] = [];
    const paragraphs = textToConvert.trim().split('\n').filter(p => p.trim() !== '');
    if (paragraphs.length === 0) {
        setError('Text contains no convertible content.');
        setIsLoading(false);
        return;
    }
    
    const initialProgress = paragraphs.map(p => ({ line: p, status: 'pending' as const }));
    setProgress(initialProgress);

    try {
        for(let i = 0; i < paragraphs.length; i++) {
            if (isPaused) {
                setStatusMessage('Conversion paused by user.');
                setIsLoading(false);
                setProgress(prev => prev.map(p => p.status === 'processing' ? { ...p, status: 'pending'} : p));
                return;
            }
            const paragraph = paragraphs[i];
            
            setProgress(prev => prev.map((item, idx) => idx === i ? { ...item, status: 'processing' } : item));
            
            let success = false;
            while (!success) {
                if (currentKeyIndexRef.current >= keysToUse.length) {
                    throw new Error('All available API keys failed.');
                }
                const currentKey = keysToUse[currentKeyIndexRef.current];

                setStatusMessage(`Converting subtitle ${i + 1}/${paragraphs.length} with key #${currentKeyIndexRef.current + 1}...`);
                
                try {
                    const requestBody = {
                        text: paragraph,
                        model_id: modelId,
                        voice_settings: {
                            stability: stability / 100,
                            similarity_boost: similarity / 100,
                            style: 0.5,
                            use_speaker_boost: true,
                        }
                    };

                    const response = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${voiceId}?output_format=mp3_44100_128`, {
                        method: 'POST',
                        headers: {
                            'Accept': 'audio/mpeg',
                            'Content-Type': 'application/json',
                            'xi-api-key': currentKey,
                        },
                        body: JSON.stringify(requestBody),
                    });

                    if (!response.ok) {
                        const errorData = await response.json().catch(() => ({}));
                        throw new Error(errorData.detail?.message || `API Error (status ${response.status})`);
                    }
                    const audioBlob = await response.blob();
                    audioBlobs.push(audioBlob);
                    success = true; // Succeeded, break while loop for this paragraph
                    setProgress(prev => prev.map((item, idx) => idx === i ? { ...item, status: 'done' } : item));

                } catch(err) {
                    const errorMsg = (err as Error).message;
                    const nextKeyIndex = currentKeyIndexRef.current + 1;
                    setStatusMessage(`Key #${currentKeyIndexRef.current + 1} failed: ${errorMsg}. ` + 
                        (nextKeyIndex < keysToUse.length ? `Switching to key #${nextKeyIndex + 1}.` : 'No more keys left.'));
                    
                    currentKeyIndexRef.current++; // Move to next key
                    await new Promise(resolve => setTimeout(resolve, 250));
                }
            }
        }

        if (audioBlobs.length > 0) {
            const mergedBlob = new Blob(audioBlobs, { type: 'audio/mpeg' });
            const url = URL.createObjectURL(mergedBlob);
            setFinalAudioUrl(url);
            setStatusMessage(`Successfully converted ${paragraphs.length} subtitles. Ready to download.`);

        } else if (!isPaused) {
             setStatusMessage('Conversion finished, but no audio was generated.');
        }

    } catch (err) {
        const errorMsg = (err as Error).message;
        setError(errorMsg);
        setStatusMessage('An error occurred during conversion.');
        // Mark the failing line as 'error'
        setProgress(prev => prev.map(p => p.status === 'processing' ? { ...p, status: 'error' } : p));
    } finally {
        setIsLoading(false);
        setIsPaused(false); // Reset pause state for the next run
    }
  };

  const handleDownload = async () => {
    if (!finalAudioUrl) return;
  
    try {
      // Modern approach: File System Access API for "Save As..." dialog
      if (window.showSaveFilePicker) {
        const fileHandle = await window.showSaveFilePicker({
          suggestedName: 'complete_audio.mp3',
          types: [{
            description: 'MP3 Audio File',
            accept: { 'audio/mpeg': ['.mp3'] },
          }],
        });
  
        // Fetch the blob data from the object URL
        const response = await fetch(finalAudioUrl);
        const blob = await response.blob();
  
        const writable = await fileHandle.createWritable();
        await writable.write(blob);
        await writable.close();
        setStatusMessage('File saved successfully.');
      } else {
        // Trigger fallback for browsers that don't support it
        throw new Error('`showSaveFilePicker` is not supported.');
      }
    } catch (err) {
      // This catch block handles both API errors and the fallback case for older browsers
      console.log('Using fallback download method because: ', (err as Error).message);
      const a = document.createElement('a');
      a.href = finalAudioUrl;
      a.download = 'complete_audio.mp3';
      document.body.appendChild(a);
a.click();
      document.body.removeChild(a);
      setStatusMessage('Download initiated. Check your browser downloads.');
    }
  };
  
  const statusMap: { [key in ProgressItem['status']]: string } = {
    pending: 'Chờ',
    processing: 'Đang xử lý...',
    done: 'Xong',
    error: 'Lỗi'
  };

  const currentActiveKey = apiKey || apiKeysFromFile[0] || '';


  return (
    <>
      <header className="app-header">
        <h1>AI VOICE TÀI LÊ MMO</h1>
        <p>Liên hệ: 0394342601</p>
      </header>
      <div className="app-container">
        {/* Left Panel */}
        <div className="panel panel-left">
          <fieldset>
            <legend>API Keys</legend>
            <div className="form-group">
              <label htmlFor="api-key">Enter Single API Key</label>
              <input 
                id="api-key" 
                type="password" 
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="Used if no file is loaded"
              />
            </div>
            <div className="form-group">
              <label htmlFor="api-keys-file">Or Load API Keys From File</label>
              <div className="form-row">
                <input id="api-keys-file-display" type="text" readOnly className="flex-grow file-input-display" value={apiKeyFileName} placeholder="One key per line (.txt)" onClick={() => apiKeyFileInputRef.current?.click()} />
                <button onClick={() => apiKeyFileInputRef.current?.click()} disabled={isCheckingApiKeys}>
                    Browse {isCheckingApiKeys && <span className="spinner"></span>}
                </button>
                <input type="file" ref={apiKeyFileInputRef} onChange={handleApiKeyFileChange} style={{ display: 'none' }} accept=".txt" />
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>Proxy</legend>
            <div className="form-row">
                <input id="use-proxy" type="checkbox" checked={useProxy} onChange={e => setUseProxy(e.target.checked)} />
                <label htmlFor="use-proxy">Use Proxy</label>
            </div>
            <div className="form-group">
                <label htmlFor="proxy-list">Proxy List:</label>
                <textarea id="proxy-list" rows={5} value={proxyList} onChange={e => setProxyList(e.target.value)}></textarea>
            </div>
          </fieldset>
          
          <fieldset>
             <legend>Voice</legend>
              <div className="form-row">
                <input 
                  type="text" 
                  placeholder="Paste Voice URL or enter ID..." 
                  className="flex-grow"
                  value={voiceSearchInput}
                  onChange={e => setVoiceSearchInput(e.target.value)}
                  onPaste={handleVoiceSearchPaste}
                />
                <button onClick={() => handleFindVoice(voiceSearchInput)} disabled={isFindingVoice || !currentActiveKey || !voiceSearchInput}>
                    LẤY ID VOICE {isFindingVoice && <span className="spinner"></span>}
                </button>
              </div>
              <div className="form-row">
                <select id="voice-select" className="flex-grow" value={voiceId} onChange={e => setVoiceId(e.target.value)} disabled={availableVoices.length === 0}>
                    {availableVoices.length === 0 && <option>--Load voices first--</option>}
                    {availableVoices.map(v => (
                      <option key={v.voice_id} value={v.voice_id}>{getVoiceDisplayName(v)}</option>
                    ))}
                </select>
                <button onClick={() => handleCheckApiKey(currentActiveKey)} disabled={!currentActiveKey || isFetchingVoices}>
                    Load All Library Voices {isFetchingVoices && <span className="spinner"></span>}
                </button>
              </div>
              <div className="form-group">
                  <label htmlFor="model-select">Model:</label>
                  <select id="model-select" value={modelId} onChange={e => setModelId(e.target.value)}>
                      <option value="eleven_multilingual_v2">Eleven Multilingual v2</option>
                      <option value="eleven_multilingual_v1">Eleven Multilingual v1</option>
                      <option value="eleven_monolingual_v1">Eleven Monolingual v1</option>
                  </select>
              </div>
               <div className="form-row">
                    <label htmlFor="voice-speed">Voice Speed:</label>
                    <input 
                      id="voice-speed" 
                      type="number" 
                      value={voiceSpeed} 
                      onChange={e => setVoiceSpeed(parseFloat(e.target.value))} 
                      step="0.05" 
                      min="0.50"
                      max="1.30"
                      title="Value must be between 0.50 and 1.30"
                    />
               </div>
               <div className="form-row">
                    <label htmlFor="stability">Stability (%):</label>
                    <input id="stability" type="number" value={stability} onChange={e => setStability(parseInt(e.target.value))} min="0" max="100" />
               </div>
               <div className="form-row">
                    <label htmlFor="similarity">Similarity (%):</label>
                    <input id="similarity" type="number" value={similarity} onChange={e => setSimilarity(parseInt(e.target.value))} min="0" max="100" />
               </div>
               <div className="form-row">
                    <input id="add-delay" type="checkbox" checked={addDelay} onChange={e => setAddDelay(e.target.checked)} />
                    <label htmlFor="add-delay">Add Delay (non-SRT):</label>
                    <input id="delay" type="number" value={delay} onChange={e => setDelay(parseFloat(e.target.value))} step="0.01" disabled={!addDelay} />
                    <span>(s)</span>
               </div>
          </fieldset>
        </div>

        {/* Right Panel */}
        <div className="panel panel-right">
            <fieldset className="flex-grow no-gap">
                <legend>Direct Text Input:</legend>
                <div className="textarea-container">
                  <textarea 
                    placeholder="Enter text directly here, one subtitle per line; use <#0.5#> for 0.5s pauses" 
                    style={{height: '200px'}}
                    value={directText}
                    onChange={e => {
                      setDirectText(e.target.value);
                      setProgress([]);
                    }}
                  ></textarea>
                  <div className="textarea-footer">
                    <span 
                      className="char-counter"
                      title={
                        totalRemainingChars !== null
                          ? `Total remaining characters from ${apiKeysFromFile.length} keys`
                          : userInfo 
                            ? `Characters remaining on this key (Tier: ${userInfo.subscription.tier})`
                            : 'Typed characters'
                      }
                    >
                      {`${directText.length.toLocaleString('vi-VN')}`}
                      {totalRemainingChars !== null 
                        ? ` / ${totalRemainingChars.toLocaleString('vi-VN')}`
                        : userInfo 
                          ? ` / ${(userInfo.subscription.character_limit - userInfo.subscription.character_count).toLocaleString('vi-VN')}`
                          : ''
                      }
                      {' ký tự'}
                    </span>
                  </div>
                </div>
            </fieldset>
             <fieldset className="flex-grow no-gap">
                <legend>Subtitles:</legend>
                 <div className="split-controls">
                    <label htmlFor="split-delimiters">Tách đoạn theo dấu:</label>
                    <input
                      id="split-delimiters"
                      type="text"
                      value={splitDelimiters}
                      onChange={e => setSplitDelimiters(e.target.value)}
                      className="flex-grow"
                      title="Tách văn bản trong ô 'Direct Text Input' thành các dòng riêng biệt dựa trên các ký tự này."
                    />
                    <button onClick={handleSplitText}>Tách đoạn</button>
                 </div>
                 <div className="form-row">
                    <label>Select a text or SRT file</label>
                    <input id="srt-file-display" type="text" readOnly className="flex-grow file-input-display" value={srtFilePath} onClick={() => srtFileInputRef.current?.click()}/>
                    <button onClick={() => srtFileInputRef.current?.click()}>Browse</button>
                    <input type="file" ref={srtFileInputRef} onChange={handleSrtFileChange} style={{ display: 'none' }} accept=".txt,.srt" />
                </div>
                <textarea
                  readOnly
                  style={{
                    height: progress.length > 0 ? '121px' : '250px',
                    backgroundColor: 'var(--disabled-bg-color)',
                    transition: 'height 0.3s ease',
                    marginBottom: progress.length > 0 ? '8px' : '0'
                  }}
                  value={subtitles}
                  placeholder="Nội dung từ file text hoặc SRT sẽ hiện ở đây."
                ></textarea>
                {progress.length > 0 && (
                    <div className="progress-display" style={{ height: '121px' }}>
                        {progress.map((item, index) => (
                            <div key={index} className={`progress-item status-${item.status}`}>
                                <span className="progress-text" title={item.line}>
                                    {`${index + 1}. ${item.line}`}
                                </span>
                                <span className="status-indicator">
                                    {statusMap[item.status]}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </fieldset>
        </div>
      </div>
       {progress.length > 0 && (
        <div className="progress-bar-container" title={`Overall Progress: ${conversionProgress}%`}>
          <div className="progress-bar" style={{ width: `${conversionProgress}%` }}>
            {conversionProgress > 5 && `${conversionProgress}%`}
          </div>
        </div>
      )}
      <div className="config-actions">
        <button onClick={handleSaveConfig}>Lưu cấu hình</button>
        <button onClick={() => handleLoadConfig(false)}>Tải cấu hình</button>
        <button onClick={handleDeleteConfig}>Xóa cấu hình</button>
      </div>
      <div className="main-actions">
          <button onClick={handleSynthesize} disabled={isLoading}>{isLoading ? 'Converting...' : 'Convert to Audio'}</button>
          <button onClick={handlePause} disabled={!isLoading}>Pause</button>
      </div>
      {finalAudioUrl && (
        <div className="output-controls">
          <div className="audio-player">
            <label htmlFor="audio-preview">Nghe thử:</label>
            <audio id="audio-preview" controls src={finalAudioUrl}></audio>
          </div>
          <button onClick={handleDownload} className="button-like">
            Tải về
          </button>
        </div>
      )}
      <div className={`status-bar ${error ? 'status-error' : ''}`}>
        {error ? `Error: ${error}` : statusMessage}
      </div>
      {isInfoModalOpen && foundVoiceInfo && (
        <div className="modal-overlay">
          <div className="modal-content">
            <button className="modal-close-button" onClick={() => setIsInfoModalOpen(false)}>&times;</button>
            <h3>Voice Details: {foundVoiceInfo.name}</h3>
            {foundVoiceInfo.description && <p><strong>Description:</strong> {foundVoiceInfo.description}</p>}
            {foundVoiceInfo.category && <p><strong>Category:</strong> <span style={{textTransform: 'capitalize'}}>{foundVoiceInfo.category}</span></p>}
            {foundVoiceInfo.labels && Object.keys(foundVoiceInfo.labels).length > 0 && (
              <div>
                <strong>Labels:</strong>
                <ul>
                  {Object.entries(foundVoiceInfo.labels).map(([key, value]) => (
                    <li key={key}><span style={{textTransform: 'capitalize'}}>{key.replace('_', ' ')}</span>: {value}</li>
                  ))}
                </ul>
              </div>
            )}
            {foundVoiceInfo.preview_url && (
              <div className="audio-player modal-audio-player">
                <label>Preview:</label>
                <audio controls src={foundVoiceInfo.preview_url} style={{width: '100%'}}>
                  Your browser does not support the audio element.
                </audio>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
};

const rootElement = document.getElementById('root');
if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
