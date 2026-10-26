import { useEffect, useState, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { jsPDF } from "jspdf";

import Sidebar from "../components/Sidebar";
import { useAuth } from "../context/AuthContext";
import API from "../api/api";
import lensLogo from "../assets/lenslogo.png";

function formatFileSize(bytes) {
  if (!bytes) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function parseTranscriptSegments(rawText) {
  if (!rawText) return [];
  const lines = rawText.split("\n");
  const segments = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // Matches: [00:15] Speaker 1: text OR [00:15] John: text OR [00:15] text
    const match = trimmed.match(/^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?:([^:\n]+):)?\s*(.*)$/);
    if (match) {
      segments.push({
        timestamp: match[1],
        speaker: match[2]?.trim() || null,
        text: match[3]?.trim() || "",
      });
    } else {
      const spkMatch = trimmed.match(/^([A-Za-z0-9\s_-]{2,20}):\s+(.*)$/);
      if (spkMatch) {
        segments.push({
          timestamp: null,
          speaker: spkMatch[1].trim(),
          text: spkMatch[2].trim(),
        });
      } else {
        segments.push({
          timestamp: null,
          speaker: null,
          text: trimmed,
        });
      }
    }
  }
  return segments;
}

function Dashboard() {
  const { user, logout, authLoading } = useAuth();
  const navigate = useNavigate();

  const [darkMode, setDarkMode] = useState(() => {
    return localStorage.getItem("lensai_dark_mode") === "true";
  });

  const [inputMode, setInputMode] = useState("link"); // "link" | "upload"
  const [source, setSource] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const fileInputRef = useRef(null);

  const [language, setLanguage] = useState("english");
  const [outputFormat, setOutputFormat] = useState("translated");
  const [chatOutputFormat, setChatOutputFormat] = useState("translated");
  const [convertingFormat, setConvertingFormat] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const [meetings, setMeetings] = useState([]);
  const [selectedMeeting, setSelectedMeeting] = useState(null);
  const [chats, setChats] = useState([]);

  const [activeView, setActiveView] = useState("home");
  const [processing, setProcessing] = useState(false);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  const [copied, setCopied] = useState(false);
  const [transcriptFilter, setTranscriptFilter] = useState("");

  useEffect(() => {
    localStorage.setItem("lensai_dark_mode", darkMode);
  }, [darkMode]);

  useEffect(() => {
    if (!authLoading && !user) {
      navigate("/login");
    }
  }, [user, authLoading, navigate]);

  const fetchMeetings = async () => {
    try {
      const res = await API.get("/meetings/");
      setMeetings(res.data);
    } catch (err) {
      if (err.response?.status === 401) {
        logout();
        navigate("/login");
      }
    }
  };

  useEffect(() => {
    fetchMeetings();
  }, []);

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  const handleNewMeeting = () => {
    setSelectedMeeting(null);
    setChats([]);
    setSource("");
    setSelectedFile(null);
    setUploadProgress(0);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    setQuestion("");
    setActiveView("home");
    setError("");
  };

  const handleDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer?.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      setSelectedFile(file);
      setInputMode("upload");
      setError("");
    }
  };

  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      setSelectedFile(e.target.files[0]);
      setError("");
    }
  };

  const handleRemoveFile = (e) => {
    e.stopPropagation();
    setSelectedFile(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const loadMeeting = async (meetingId, view = "summary") => {
    try {
      const meetingRes = await API.get(`/meetings/${meetingId}`);
      setSelectedMeeting(meetingRes.data);
      if (meetingRes.data.output_format) {
        setChatOutputFormat(meetingRes.data.output_format);
      }

      const chatRes = await API.get(`/meetings/${meetingId}/chats`);
      setChats(chatRes.data);

      setActiveView(view === "chat" ? "summary" : view);
      setError("");
    } catch {
      setError("Failed to load meeting details");
    }
  };

  const handleFormatChange = async (newFormat) => {
    if (!selectedMeeting || (selectedMeeting.output_format || "translated") === newFormat || convertingFormat) return;

    try {
      setConvertingFormat(true);
      setError("");
      const res = await API.post(`/meetings/${selectedMeeting.id}/format`, {
        output_format: newFormat,
      });
      setSelectedMeeting(res.data);
      setChatOutputFormat(newFormat);
      await fetchMeetings();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to switch meeting format");
    } finally {
      setConvertingFormat(false);
    }
  };

  const handleDeleteMeeting = async (meetingId) => {
    const confirmDelete = window.confirm(
      "Are you sure you want to delete this meeting?"
    );
    if (!confirmDelete) return;

    try {
      await API.delete(`/meetings/${meetingId}`);
      if (selectedMeeting?.id === meetingId) {
        handleNewMeeting();
      }
      await fetchMeetings();
    } catch {
      setError("Failed to delete meeting");
    }
  };

  const handleRenameMeeting = async (meetingId, title) => {
    try {
      await API.patch(`/meetings/${meetingId}/rename`, { title });
      await fetchMeetings();
      if (selectedMeeting && selectedMeeting.id === meetingId) {
        await loadMeeting(meetingId);
      }
    } catch {
      setError("Failed to rename meeting");
    }
  };

  const handleProcessMeeting = async () => {
    if (inputMode === "link" && !source.trim()) return;
    if (inputMode === "upload" && !selectedFile) return;

    try {
      setProcessing(true);
      setUploadProgress(0);
      setError("");

      let res;
      if (inputMode === "upload") {
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("language", language);
        formData.append("output_format", outputFormat);

        res = await API.post("/meetings/upload", formData, {
          headers: {
            "Content-Type": "multipart/form-data",
          },
          onUploadProgress: (progressEvent) => {
            if (progressEvent.total) {
              const percent = Math.round(
                (progressEvent.loaded * 100) / progressEvent.total
              );
              setUploadProgress(percent);
            }
          },
        });
      } else {
        res = await API.post("/meetings/process", {
          source,
          language,
          output_format: outputFormat,
        });
      }

      await fetchMeetings();
      await loadMeeting(res.data.meeting_id, "summary");
      setSource("");
      setSelectedFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to process meeting");
    } finally {
      setProcessing(false);
      setUploadProgress(0);
    }
  };

  // Real-time Streaming AI Chat
  const handleAskQuestion = async () => {
    if (!question.trim() || !selectedMeeting || asking) return;

    const userQuestion = question;
    const tempChatId = "temp-" + Date.now();

    try {
      setAsking(true);
      setError("");
      setQuestion("");

      // Optimistically show user message and empty assistant reply
      setChats((prev) => [
        ...prev,
        {
          id: tempChatId,
          question: userQuestion,
          answer: "",
          created_at: new Date().toISOString(),
        },
      ]);

      const token = localStorage.getItem("token");
      const baseUrl = API.defaults.baseURL || "http://127.0.0.1:8000";
      const response = await fetch(
        `${baseUrl}/meetings/${selectedMeeting.id}/ask/stream`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            question: userQuestion,
            output_format: chatOutputFormat,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`Server returned status ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let streamText = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        streamText += chunk;

        setChats((prev) =>
          prev.map((c) =>
            c.id === tempChatId ? { ...c, answer: streamText } : c
          )
        );
      }

      await fetchMeetings();
    } catch (err) {
      setError("Failed to get answer: " + (err.message || "Network error"));
      setChats((prev) => prev.filter((c) => c.id !== tempChatId));
      setQuestion(userQuestion);
    } finally {
      setAsking(false);
    }
  };

  // Markdown Export generator
  const generateMeetingMarkdown = () => {
    if (!selectedMeeting) return "";
    const dateStr = selectedMeeting.created_at
      ? new Date(selectedMeeting.created_at).toLocaleString()
      : new Date().toLocaleString();

    let md = `# ${selectedMeeting.title || "Meeting Notes"}\n\n`;
    md += `**Date:** ${dateStr}  \n`;
    md += `**Source:** ${selectedMeeting.source}  \n\n`;
    md += `---\n\n`;

    md += `## 📋 Summary\n\n${selectedMeeting.summary || "No summary available."}\n\n`;

    if (selectedMeeting.action_items?.length) {
      md += `## ✅ Action Items\n\n`;
      selectedMeeting.action_items.forEach((item) => {
        md += `- [ ] ${item}\n`;
      });
      md += `\n`;
    }

    if (selectedMeeting.key_decisions?.length) {
      md += `## 🎯 Key Decisions\n\n`;
      selectedMeeting.key_decisions.forEach((item) => {
        md += `- ${item}\n`;
      });
      md += `\n`;
    }

    if (selectedMeeting.open_questions?.length) {
      md += `## ❓ Open Questions\n\n`;
      selectedMeeting.open_questions.forEach((item) => {
        md += `- ${item}\n`;
      });
      md += `\n`;
    }

    if (selectedMeeting.transcript) {
      md += `## 🎙️ Transcript\n\n${selectedMeeting.transcript}\n`;
    }

    return md;
  };

  const handleCopyMarkdown = async () => {
    const md = generateMeetingMarkdown();
    if (!md) return;
    await navigator.clipboard.writeText(md);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  };

  // PDF Export using jsPDF
  const handleDownloadPdf = () => {
    if (!selectedMeeting) return;
    const doc = new jsPDF({
      unit: "pt",
      format: "letter",
    });

    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const margin = 45;
    const maxLineWidth = pageWidth - margin * 2;
    let cursorY = margin + 15;

    const checkPageBreak = (neededHeight) => {
      if (cursorY + neededHeight > pageHeight - margin) {
        doc.addPage();
        cursorY = margin + 15;
      }
    };

    // Brand & Title
    doc.setFont("helvetica", "bold");
    doc.setFontSize(20);
    doc.setTextColor(30, 30, 45);
    const titleLines = doc.splitTextToSize(selectedMeeting.title || "Meeting Notes", maxLineWidth);
    checkPageBreak(titleLines.length * 24);
    doc.text(titleLines, margin, cursorY);
    cursorY += titleLines.length * 24 + 4;

    // Subtitle / Date
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.setTextColor(120, 120, 135);
    const dateStr = selectedMeeting.created_at ? new Date(selectedMeeting.created_at).toLocaleString() : "";
    doc.text(`LensAI Meeting Intelligence  •  ${dateStr}`, margin, cursorY);
    cursorY += 15;
    doc.text(`Source: ${selectedMeeting.source.slice(0, 85)}`, margin, cursorY);
    cursorY += 18;

    // Divider Line
    doc.setDrawColor(215, 215, 230);
    doc.setLineWidth(1);
    doc.line(margin, cursorY, pageWidth - margin, cursorY);
    cursorY += 22;

    const addSection = (heading, content, isList = false) => {
      checkPageBreak(35);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(12);
      doc.setTextColor(124, 77, 255);
      doc.text(heading, margin, cursorY);
      cursorY += 16;

      doc.setFont("helvetica", "normal");
      doc.setFontSize(10);
      doc.setTextColor(50, 50, 65);

      if (isList && Array.isArray(content)) {
        if (content.length === 0) {
          checkPageBreak(16);
          doc.text("None recorded", margin + 10, cursorY);
          cursorY += 16;
        } else {
          content.forEach((item) => {
            const itemLines = doc.splitTextToSize(`•  ${item}`, maxLineWidth - 10);
            checkPageBreak(itemLines.length * 14 + 4);
            doc.text(itemLines, margin + 10, cursorY);
            cursorY += itemLines.length * 14 + 4;
          });
        }
        cursorY += 12;
      } else if (typeof content === "string") {
        const plainText = content.replace(/^#+\s+/gm, "");
        const lines = doc.splitTextToSize(plainText, maxLineWidth);
        checkPageBreak(lines.length * 14);
        doc.text(lines, margin, cursorY);
        cursorY += lines.length * 14 + 14;
      }
    };

    addSection("EXECUTIVE SUMMARY", selectedMeeting.summary);
    addSection("ACTION ITEMS", selectedMeeting.action_items, true);
    addSection("KEY DECISIONS", selectedMeeting.key_decisions, true);
    addSection("OPEN QUESTIONS", selectedMeeting.open_questions, true);

    const safeFilename = (selectedMeeting.title || "meeting_notes")
      .replace(/[^a-z0-9]/gi, "_")
      .toLowerCase()
      .slice(0, 35);
    doc.save(`${safeFilename}.pdf`);
  };

  const filteredMeetings = useMemo(() => {
    return meetings.filter((meeting) => {
      const text = `${meeting.title || ""} ${meeting.source || ""}`.toLowerCase();
      return text.includes(searchQuery.toLowerCase());
    });
  }, [meetings, searchQuery]);

  const parsedTranscript = useMemo(() => {
    return parseTranscriptSegments(selectedMeeting?.transcript || "");
  }, [selectedMeeting?.transcript]);

  const filteredTranscript = useMemo(() => {
    if (!transcriptFilter.trim()) return parsedTranscript;
    const q = transcriptFilter.toLowerCase();
    return parsedTranscript.filter(
      (s) =>
        s.text.toLowerCase().includes(q) ||
        (s.speaker && s.speaker.toLowerCase().includes(q)) ||
        (s.timestamp && s.timestamp.includes(q))
    );
  }, [parsedTranscript, transcriptFilter]);

  const isYoutube =
    source.includes("youtube.com") || source.includes("youtu.be");

  return (
    <div
      className={`relative flex h-screen overflow-hidden ${
        darkMode ? "bg-[#0A0A12] text-[#F5F3FF]" : "bg-[#F6F3FB] text-[#1B1B2A]"
      }`}
    >
      <div className="pointer-events-none absolute -left-24 -top-20 h-[420px] w-[420px] rounded-full bg-[#7C4DFF]/20 blur-3xl" />
      <div className="pointer-events-none absolute right-0 top-1/3 h-[380px] w-[380px] rounded-full bg-[#FF7A59]/15 blur-3xl" />
      <div className="pointer-events-none absolute bottom-0 left-1/3 h-[300px] w-[300px] rounded-full bg-[#B355FF]/10 blur-3xl" />

      <Sidebar
        user={user}
        darkMode={darkMode}
        logout={handleLogout}
        meetings={filteredMeetings}
        selectedMeeting={selectedMeeting}
        onNewMeeting={handleNewMeeting}
        onSelectMeeting={(id) => loadMeeting(id, "summary")}
        onSelectChat={(id) => loadMeeting(id, "chat")}
        onDeleteMeeting={handleDeleteMeeting}
        onRenameMeeting={handleRenameMeeting}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
      />

      <main className="relative z-10 flex flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-end gap-3 px-8 py-5">
          <button
            onClick={() => setDarkMode(!darkMode)}
            title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
            className={`flex h-10 w-10 items-center justify-center rounded-full border text-base transition ${
              darkMode
                ? "border-[#2A2E3A] bg-[#14141F] hover:border-[#7C4DFF]"
                : "border-[#E7E2F5] bg-white shadow-sm hover:border-[#7C4DFF]"
            }`}
          >
            {darkMode ? "☀️" : "🌙"}
          </button>

          <div
            className={`flex items-center gap-3 rounded-full py-1.5 pl-1.5 pr-4 shadow-sm ${
              darkMode
                ? "bg-[#14141F] border border-[#2A2E3A]"
                : "bg-white border border-[#E7E2F5]"
            }`}
          >
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-[#7C4DFF] to-[#FF7A59] text-xs font-bold text-white">
              {user?.name?.[0]?.toUpperCase() || "U"}
            </div>
            <div className="leading-tight">
              <p className="text-sm font-semibold">{user?.name || "User"}</p>
              <p className="text-[11px] text-slate-500">{user?.email}</p>
            </div>
          </div>
        </header>

        <section className="flex flex-1 flex-col overflow-hidden px-8 pb-8">
          {activeView === "home" && (
            <div className="flex flex-1 items-center justify-center">
              <div
                className={`grid w-full max-w-4xl grid-cols-1 overflow-hidden rounded-[24px] border shadow-2xl md:grid-cols-[0.85fr_1.15fr] ${
                  darkMode ? "border-[#2A2E3A] bg-[#12151D]" : "border-[#E7E2F5] bg-white"
                }`}
              >
                <div className="hidden flex-col justify-between bg-gradient-to-br from-[#7C4DFF] to-[#FF7A59] p-8 md:flex">
                  <img
                    src={lensLogo}
                    alt="LensAI"
                    className="h-12 w-12 rounded-2xl object-cover shadow-md"
                  />

                  <div>
                    <h2 className="text-2xl font-black leading-tight text-white">
                      Bring any meeting or video into focus.
                    </h2>
                    <p className="mt-3 text-sm text-white/80">
                      Paste a YouTube URL or a local media path. LensAI instantly extracts captions,
                      transcribes audio with Whisper, attributes speakers, and equips an interactive assistant.
                    </p>
                  </div>

                  <div className="rounded-xl bg-white/15 px-4 py-3 text-white backdrop-blur">
                    <p className="text-2xl font-bold">{meetings.length}</p>
                    <p className="text-xs text-white/70">meetings processed so far</p>
                  </div>
                </div>

                <div className="p-8">
                  <div className="mb-6 flex items-center gap-2 md:hidden">
                    <img
                      src={lensLogo}
                      alt="LensAI"
                      className="h-9 w-9 rounded-xl object-cover shadow-sm"
                    />
                    <span className="text-lg font-bold">LensAI</span>
                  </div>

                  <h1 className="text-2xl font-bold">New meeting</h1>
                  <p className={`mt-1 text-sm ${darkMode ? "text-slate-400" : "text-slate-500"}`}>
                    Paste a YouTube URL or a meeting recording path.
                  </p>

                  {/* Input Mode Switcher: YouTube Link vs Upload Video */}
                  <div className="mt-5 flex items-center gap-1.5 p-1 rounded-xl border border-inherit bg-slate-500/5">
                    <button
                      type="button"
                      onClick={() => setInputMode("link")}
                      className={`flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-xs font-semibold transition ${
                        inputMode === "link"
                          ? "bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] text-white shadow-sm"
                          : darkMode
                          ? "text-slate-400 hover:text-white"
                          : "text-slate-600 hover:text-slate-900"
                      }`}
                    >
                      <span>🔗</span> YouTube URL
                    </button>
                    <button
                      type="button"
                      onClick={() => setInputMode("upload")}
                      className={`flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-xs font-semibold transition ${
                        inputMode === "upload"
                          ? "bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] text-white shadow-sm"
                          : darkMode
                          ? "text-slate-400 hover:text-white"
                          : "text-slate-600 hover:text-slate-900"
                      }`}
                    >
                      <span>📁</span> Upload / Drag & Drop
                    </button>
                  </div>

                  {/* Mode 1: YouTube URL */}
                  {inputMode === "link" ? (
                    <div className="mt-4">
                      <textarea
                        value={source}
                        onChange={(e) => setSource(e.target.value)}
                        rows={4}
                        placeholder="https://youtube.com/watch?v=... or https://youtu.be/..."
                        className={`w-full resize-none rounded-xl border px-4 py-3 text-sm outline-none transition ${
                          darkMode
                            ? "border-[#2A2E3A] bg-[#0A0A12] text-white placeholder:text-slate-500 focus:border-[#7C4DFF]"
                            : "border-[#E7E2F5] bg-[#F6F3FB] text-slate-900 placeholder:text-slate-400 focus:border-[#7C4DFF]"
                        }`}
                      />

                      <p className="mt-1.5 text-xs text-slate-500">
                        {source
                          ? isYoutube
                            ? "⚡ YouTube detected (uses instant caption fast-path if available)"
                            : "Custom web video URL"
                          : "Paste any YouTube link to fetch captions or transcribe audio"}
                      </p>
                    </div>
                  ) : (
                    /* Mode 2: Drag & Drop Video/Audio File Upload */
                    <div className="mt-4">
                      <input
                        type="file"
                        ref={fileInputRef}
                        onChange={handleFileSelect}
                        accept="video/*,audio/*,.mp4,.mkv,.webm,.mov,.avi,.mp3,.wav,.m4a,.aac,.flac"
                        className="hidden"
                      />

                      {!selectedFile ? (
                        <div
                          onDragEnter={handleDragEnter}
                          onDragOver={handleDragOver}
                          onDragLeave={handleDragLeave}
                          onDrop={handleDrop}
                          onClick={() => fileInputRef.current?.click()}
                          className={`group relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed p-7 text-center cursor-pointer transition ${
                            isDragging
                              ? "border-[#7C4DFF] bg-[#7C4DFF]/15 scale-[1.01]"
                              : darkMode
                              ? "border-[#2A2E3A] bg-[#0A0A12]/80 hover:border-[#7C4DFF] hover:bg-[#7C4DFF]/5"
                              : "border-[#D8D2EB] bg-[#F6F3FB]/70 hover:border-[#7C4DFF] hover:bg-[#7C4DFF]/5"
                          }`}
                        >
                          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-[#7C4DFF]/20 to-[#FF7A59]/20 text-2xl transition group-hover:scale-110">
                            {isDragging ? "📥" : "🎬"}
                          </div>

                          <p className="mt-3 text-sm font-semibold">
                            {isDragging ? (
                              <span className="text-[#7C4DFF]">Drop your video or audio file here!</span>
                            ) : (
                              <>
                                Drag and drop downloaded video/audio here, or{" "}
                                <span className="text-[#7C4DFF] underline decoration-2 underline-offset-2">
                                  browse
                                </span>
                              </>
                            )}
                          </p>
                          <p className="mt-1 text-xs text-slate-500">
                            Supports downloaded MP4, MKV, WebM, MOV, MP3, WAV, M4A, AAC
                          </p>

                          <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                            {["MP4", "MKV", "WebM", "MP3", "WAV", "M4A"].map((fmt) => (
                              <span
                                key={fmt}
                                className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                                  darkMode ? "bg-white/5 text-slate-400" : "bg-black/5 text-slate-600"
                                }`}
                              >
                                {fmt}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div
                          className={`flex items-center justify-between rounded-2xl border p-4 shadow-sm ${
                            darkMode ? "border-[#2A2E3A] bg-[#0A0A12]" : "border-[#E7E2F5] bg-white"
                          }`}
                        >
                          <div className="flex items-center gap-3 overflow-hidden">
                            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-[#7C4DFF] to-[#FF7A59] text-xl text-white shadow-sm">
                              {selectedFile.type?.startsWith("audio") ? "🎵" : "🎥"}
                            </div>
                            <div className="overflow-hidden">
                              <p className="truncate text-sm font-semibold leading-tight">
                                {selectedFile.name}
                              </p>
                              <p className="text-xs text-slate-500 mt-0.5">
                                {formatFileSize(selectedFile.size)} • {selectedFile.type || "Media file"}
                              </p>
                            </div>
                          </div>

                          <div className="flex items-center gap-2 shrink-0">
                            <button
                              type="button"
                              onClick={() => fileInputRef.current?.click()}
                              className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition ${
                                darkMode
                                  ? "border-[#2A2E3A] hover:border-slate-500 text-slate-300"
                                  : "border-[#E7E2F5] hover:border-slate-400 text-slate-700"
                              }`}
                            >
                              Change
                            </button>
                            <button
                              type="button"
                              onClick={handleRemoveFile}
                              className="flex h-8 w-8 items-center justify-center rounded-lg border border-red-500/20 bg-red-500/10 text-xs font-bold text-red-400 hover:bg-red-500/20 transition"
                              title="Remove file"
                            >
                              ✕
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 block mb-1.5">
                        Spoken Audio Language
                      </label>
                      <select
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                        className={`w-full rounded-xl border px-3 py-2 text-sm outline-none ${
                          darkMode
                            ? "border-[#2A2E3A] bg-[#0A0A12] text-white"
                            : "border-[#E7E2F5] bg-white text-slate-700"
                        }`}
                      >
                        <option value="english">English</option>
                        <option value="hinglish">Hinglish / Hindi-English</option>
                      </select>
                    </div>

                    <div>
                      <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 block mb-1.5">
                        Selected Output Type
                      </label>
                      <div className="text-xs text-slate-400 mt-1">
                        Select transcript, summary & answer script below
                      </div>
                    </div>
                  </div>

                  <div className="mt-4">
                    <label className="text-xs font-semibold uppercase tracking-wide text-slate-500 block mb-2">
                      Output Format (Summary, Transcript & Answers)
                    </label>
                    <div className="grid grid-cols-3 gap-2.5">
                      {[
                        { id: "translated", label: "Translated", sub: "English", icon: "🌐" },
                        { id: "hindi", label: "Hindi Font", sub: "हिन्दी फ़ॉन्ट", icon: "🇮🇳" },
                        { id: "romanized", label: "Romanized", sub: "Roman Hinglish", icon: "🔤" },
                      ].map((opt) => {
                        const isSelected = outputFormat === opt.id;
                        return (
                          <button
                            key={opt.id}
                            type="button"
                            onClick={() => setOutputFormat(opt.id)}
                            className={`flex flex-col items-center justify-center rounded-xl border p-2.5 text-center transition ${
                              isSelected
                                ? "border-[#7C4DFF] bg-[#7C4DFF]/15 text-[#7C4DFF] font-semibold shadow-sm ring-1 ring-[#7C4DFF]"
                                : darkMode
                                ? "border-[#2A2E3A] bg-[#0A0A12] text-slate-400 hover:border-slate-600 hover:text-white"
                                : "border-[#E7E2F5] bg-[#F6F3FB] text-slate-600 hover:border-slate-300 hover:text-slate-900"
                            }`}
                          >
                            <span className="text-lg">{opt.icon}</span>
                            <span className="text-xs font-semibold mt-1 leading-tight">{opt.label}</span>
                            <span className="text-[10px] opacity-75 mt-0.5">{opt.sub}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <button
                    onClick={handleProcessMeeting}
                    disabled={
                      processing ||
                      (inputMode === "link" && !source.trim()) ||
                      (inputMode === "upload" && !selectedFile)
                    }
                    className="mt-6 w-full rounded-xl bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] py-3.5 text-sm font-semibold text-white shadow-[0_0_25px_rgba(124,77,255,0.3)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {processing
                      ? inputMode === "upload" && uploadProgress < 100
                        ? `Uploading (${uploadProgress}%)...`
                        : "Processing meeting..."
                      : inputMode === "upload"
                      ? "Upload and process video"
                      : "Process meeting"}
                  </button>

                  {processing && (
                    <div
                      className={`mt-5 rounded-xl border p-4 text-left ${
                        darkMode
                          ? "border-[#2A2E3A] bg-[#0A0A12] text-slate-300"
                          : "border-[#E7E2F5] bg-[#F6F3FB] text-slate-600"
                      }`}
                    >
                      <h3 className="mb-1 text-sm font-semibold flex items-center justify-between">
                        <span>
                          {inputMode === "upload" && uploadProgress < 100
                            ? "Uploading media file..."
                            : "Analyzing and processing..."}
                        </span>
                        {inputMode === "upload" && (
                          <span className="text-xs font-bold text-[#7C4DFF]">
                            {uploadProgress}%
                          </span>
                        )}
                      </h3>

                      {inputMode === "upload" && uploadProgress < 100 && (
                        <div className="my-2 h-2 w-full overflow-hidden rounded-full bg-slate-700/20">
                          <div
                            className="h-full bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] transition-all duration-200"
                            style={{ width: `${uploadProgress}%` }}
                          />
                        </div>
                      )}

                      <p className="text-xs leading-5">
                        {inputMode === "upload" && uploadProgress < 100
                          ? `Uploading ${selectedFile?.name || "file"} to server...`
                          : "Transcribing audio with Whisper, identifying speakers, generating summary, and building vector knowledge base. This may take a moment."}
                      </p>
                    </div>
                  )}

                  {error && (
                    <p className="mt-5 rounded-lg border border-[#FF7A59]/30 bg-[#FF7A59]/10 px-4 py-3 text-sm text-[#E85D3D]">
                      {String(error)}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {selectedMeeting && activeView !== "home" && (
            <div className="flex h-full flex-col gap-5 overflow-hidden lg:flex-row">
              {/* Left Rail: meeting meta, export actions, tabs */}
              <div
                className={`flex shrink-0 flex-col justify-between gap-4 rounded-2xl border p-5 lg:w-72 ${
                  darkMode ? "border-[#2A2E3A] bg-[#12151D]" : "border-[#E7E2F5] bg-white"
                }`}
              >
                <div className="space-y-4">
                  <div>
                    <h1 className="text-lg font-black leading-tight tracking-tight">
                      {selectedMeeting.title}
                    </h1>
                    <p className="mt-1 truncate text-xs text-slate-500">
                      {selectedMeeting.source}
                    </p>
                  </div>

                  {/* Export Buttons */}
                  <div className="flex flex-col gap-2 pt-2 border-t border-inherit">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                      Export Notes
                    </span>
                    <div className="flex gap-2">
                      <button
                        onClick={handleCopyMarkdown}
                        className={`flex-1 flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition ${
                          copied
                            ? "bg-emerald-500/10 border-emerald-500/40 text-emerald-400"
                            : darkMode
                            ? "bg-[#0A0A12] border-[#2A2E3A] hover:border-[#7C4DFF]"
                            : "bg-[#F6F3FB] border-[#E7E2F5] hover:border-[#7C4DFF]"
                        }`}
                        title="Copy complete markdown meeting minutes to clipboard"
                      >
                        {copied ? "✅ Copied" : "📋 Copy MD"}
                      </button>

                      <button
                        onClick={handleDownloadPdf}
                        className={`flex-1 flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition ${
                          darkMode
                            ? "bg-[#0A0A12] border-[#2A2E3A] text-[#B355FF] hover:border-[#B355FF]"
                            : "bg-[#F6F3FB] border-[#E7E2F5] text-[#7C4DFF] hover:border-[#7C4DFF]"
                        }`}
                        title="Download structured PDF document"
                      >
                        📄 PDF
                      </button>
                    </div>
                  </div>

                  {/* Format Switcher */}
                  <div className="flex flex-col gap-2 pt-2 border-t border-inherit">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                        Content Script / Format
                      </span>
                      {convertingFormat && (
                        <span className="text-[10px] font-bold text-[#7C4DFF] animate-pulse">
                          Converting...
                        </span>
                      )}
                    </div>

                    <div className="grid grid-cols-3 gap-1">
                      {[
                        { id: "translated", label: "English", icon: "🌐" },
                        { id: "hindi", label: "हिन्दी", icon: "🇮🇳" },
                        { id: "romanized", label: "Hinglish", icon: "🔤" },
                      ].map((fmt) => {
                        const isActive = (selectedMeeting.output_format || "translated") === fmt.id;
                        return (
                          <button
                            key={fmt.id}
                            disabled={convertingFormat}
                            onClick={() => handleFormatChange(fmt.id)}
                            className={`flex items-center justify-center gap-1 rounded-lg border py-1.5 text-xs font-semibold transition ${
                              isActive
                                ? "border-[#7C4DFF] bg-[#7C4DFF] text-white shadow-sm"
                                : darkMode
                                ? "border-[#2A2E3A] bg-[#0A0A12] text-slate-300 hover:border-[#7C4DFF]"
                                : "border-[#E7E2F5] bg-[#F6F3FB] text-slate-700 hover:border-[#7C4DFF]"
                            } disabled:opacity-50`}
                            title={`Switch notes & transcript to ${fmt.label}`}
                          >
                            <span>{fmt.icon}</span>
                            <span>{fmt.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Tab Switcher */}
                  <div className="flex flex-row gap-2 pt-2 border-t border-inherit lg:flex-col">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1 hidden lg:block">
                      Views
                    </span>
                    {["summary", "transcript"].map((tab) => (
                      <button
                        key={tab}
                        onClick={() => setActiveView(tab)}
                        className={`rounded-xl px-4 py-2.5 text-left text-sm font-semibold capitalize transition ${
                          activeView === tab
                            ? "bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] text-white shadow-sm"
                            : darkMode
                            ? "bg-[#0A0A12] text-slate-300 hover:bg-[#181B26]"
                            : "bg-[#F6F3FB] text-slate-700 hover:bg-[#EDE8F7]"
                        }`}
                      >
                        {tab === "summary" ? "📋 Insights & Summary" : "🎙️ Full Transcript"}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="text-[11px] text-slate-500">
                  Meeting ID #{selectedMeeting.id}
                </div>
              </div>

              {/* Right Main Content */}
              <div
                className={`flex-1 overflow-y-auto rounded-2xl border p-6 ${
                  darkMode
                    ? "border-[#2A2E3A] bg-[#12151D]"
                    : "border-[#E7E2F5] bg-white"
                }`}
              >
                {activeView === "summary" && (
                  <div className={darkMode ? "text-slate-200" : "text-slate-800"}>
                    <div
                      className={`rounded-2xl border p-6 shadow-sm ${
                        darkMode
                          ? "border-[#2A2E3A] bg-[#0A0A12]"
                          : "border-[#E7E2F5] bg-[#F6F3FB]"
                      }`}
                    >
                      <div
                        className={`space-y-4 
                        [&_h1]:mb-3 [&_h1]:text-2xl [&_h1]:font-black
                        [&_h2]:mb-2 [&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-bold [&_h2]:text-[#7C4DFF]
                        [&_h3]:mt-4 [&_h3]:text-base [&_h3]:font-semibold
                        [&_p]:text-sm [&_p]:leading-7
                        [&_ul]:list-disc [&_ul]:pl-6 [&_li]:mb-1.5 [&_li]:text-sm`}
                      >
                        <ReactMarkdown>
                          {selectedMeeting.summary || "No summary available."}
                        </ReactMarkdown>
                      </div>
                    </div>

                    <div className="mt-6 grid gap-5 md:grid-cols-3">
                      <InfoCard
                        darkMode={darkMode}
                        title="Action Items"
                        icon="✅"
                        items={selectedMeeting.action_items}
                      />
                      <InfoCard
                        darkMode={darkMode}
                        title="Key Decisions"
                        icon="🎯"
                        items={selectedMeeting.key_decisions}
                      />
                      <InfoCard
                        darkMode={darkMode}
                        title="Open Questions"
                        icon="❓"
                        items={selectedMeeting.open_questions}
                      />
                    </div>
                  </div>
                )}

                {/* Timestamped & Diarized Transcript View */}
                {activeView === "transcript" && (
                  <div className="space-y-4">
                    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-inherit">
                      <div>
                        <h2 className="text-base font-bold">Transcript & Dialogue</h2>
                        <p className="text-xs text-slate-500">
                          {parsedTranscript.length} timestamped speech segments
                        </p>
                      </div>

                      <input
                        type="text"
                        value={transcriptFilter}
                        onChange={(e) => setTranscriptFilter(e.target.value)}
                        placeholder="Search dialogue or speaker..."
                        className={`w-full sm:w-64 px-3.5 py-1.5 text-xs rounded-xl border outline-none transition ${
                          darkMode
                            ? "bg-[#0A0A12] border-[#2A2E3A] text-white placeholder:text-slate-600 focus:border-[#7C4DFF]"
                            : "bg-white border-[#E7E2F5] text-slate-800 placeholder:text-slate-400 focus:border-[#7C4DFF]"
                        }`}
                      />
                    </div>

                    {filteredTranscript.length === 0 ? (
                      <p className="py-8 text-center text-sm text-slate-500">
                        {parsedTranscript.length === 0
                          ? "No transcript text available for this meeting."
                          : "No dialogue matches your search query."}
                      </p>
                    ) : (
                      <div className="space-y-3">
                        {filteredTranscript.map((seg, idx) => (
                          <div
                            key={idx}
                            className={`p-3.5 rounded-xl border transition ${
                              darkMode
                                ? "bg-[#0A0A12] border-[#2A2E3A]/60 hover:border-[#7C4DFF]/40"
                                : "bg-[#F6F3FB] border-[#E7E2F5] hover:border-[#7C4DFF]/40"
                            }`}
                          >
                            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                              {seg.timestamp && (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-mono font-semibold bg-[#7C4DFF]/15 text-[#9E7BFF] border border-[#7C4DFF]/30">
                                  ⏱️ {seg.timestamp}
                                </span>
                              )}
                              {seg.speaker && (
                                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-[11px] font-semibold bg-gradient-to-r from-[#FF7A59]/15 to-[#B355FF]/15 text-[#FF7A59] border border-[#FF7A59]/30">
                                  👤 {seg.speaker}
                                </span>
                              )}
                            </div>
                            <p className="text-sm leading-relaxed text-inherit font-sans">
                              {seg.text}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Real-time Streaming Chat Window */}
                <div
                  className={`mt-8 rounded-2xl border p-5 ${
                    darkMode ? "border-[#2A2E3A] bg-[#0A0A12]" : "border-[#E7E2F5] bg-[#F6F3FB]"
                  }`}
                >
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b pb-3 border-inherit">
                    <div>
                      <h2 className="text-base font-bold flex items-center gap-2">
                        <span>💬</span> Chat with this meeting
                      </h2>
                      <p className="text-[11px] text-slate-500 mt-0.5">
                        💡 Ask in English, हिन्दी, or Hinglish — AI responds in your selected format
                      </p>
                    </div>

                    <div className="flex items-center gap-1 rounded-xl border p-1 border-inherit bg-slate-500/5">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 px-1.5">
                        Output:
                      </span>
                      {[
                        { id: "translated", label: "English", icon: "🌐" },
                        { id: "hindi", label: "हिन्दी", icon: "🇮🇳" },
                        { id: "romanized", label: "Hinglish", icon: "🔤" },
                      ].map((f) => {
                        const active = chatOutputFormat === f.id;
                        return (
                          <button
                            key={f.id}
                            type="button"
                            onClick={() => setChatOutputFormat(f.id)}
                            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-semibold transition ${
                              active
                                ? "bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] text-white shadow-sm"
                                : darkMode
                                ? "text-slate-400 hover:text-white hover:bg-white/5"
                                : "text-slate-600 hover:text-slate-900 hover:bg-black/5"
                            }`}
                          >
                            <span>{f.icon}</span>
                            <span>{f.label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="space-y-4 max-h-[460px] overflow-y-auto pr-1">
                    {chats.length === 0 ? (
                      <p className="py-4 text-center text-sm text-slate-500">
                        Ask a question about this meeting's decisions, topics, or action items.
                      </p>
                    ) : (
                      chats.map((chat) => (
                        <div key={chat.id} className="space-y-2">
                          <div className="ml-auto max-w-[78%] rounded-2xl bg-gradient-to-r from-[#7C4DFF] to-[#B355FF] px-4 py-2.5 text-sm leading-6 text-white shadow-sm">
                            <p className="mb-0.5 text-[10px] text-white/70 font-semibold uppercase tracking-wider">You</p>
                            <p>{chat.question}</p>
                          </div>

                          <div
                            className={`max-w-[85%] rounded-2xl px-5 py-3 text-sm leading-7 shadow-sm ${
                              darkMode
                                ? "bg-[#12151D] text-slate-100 border border-[#2A2E3A]"
                                : "bg-white text-slate-800 border border-[#E7E2F5]"
                            }`}
                          >
                            <p className="mb-1 text-[11px] font-bold text-[#B355FF] flex items-center gap-1">
                              <span>🤖</span> LensAI Assistant
                            </p>
                            {chat.answer ? (
                              <div className="[&_p]:mb-2 [&_ul]:list-disc [&_ul]:pl-5 [&_li]:mb-1 [&_strong]:text-[#FF7A59]">
                                <ReactMarkdown>{chat.answer}</ReactMarkdown>
                              </div>
                            ) : (
                              <span className="italic text-slate-400">
                                Thinking<span className="animate-pulse">...</span>
                              </span>
                            )}
                          </div>
                        </div>
                      ))
                    )}

                    {asking && chats[chats.length - 1]?.answer && (
                      <div className="text-xs text-slate-400 animate-pulse pl-2">
                        Streaming response...
                      </div>
                    )}
                  </div>

                  <div className="mt-4 flex items-center gap-2.5">
                    <input
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          handleAskQuestion();
                        }
                      }}
                      disabled={asking}
                      placeholder="Ask in English, हिन्दी, or Hinglish (e.g. 'Key decisions kya the?')..."
                      className={`flex-1 rounded-xl border px-4 py-2.5 text-sm outline-none transition ${
                        darkMode
                          ? "border-[#2A2E3A] bg-[#12151D] text-white placeholder:text-slate-500 focus:border-[#7C4DFF]"
                          : "border-[#E7E2F5] bg-white text-slate-900 placeholder:text-slate-400 shadow-sm focus:border-[#7C4DFF]"
                      }`}
                    />

                    <button
                      onClick={handleAskQuestion}
                      disabled={!question.trim() || asking}
                      className="flex h-10 px-4 shrink-0 items-center justify-center gap-1 rounded-xl bg-gradient-to-r from-[#7C4DFF] to-[#FF7A59] text-sm font-semibold text-white shadow-md transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                      title="Send question"
                    >
                      {asking ? "..." : "Ask ➤"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

function InfoCard({ darkMode, title, icon, items }) {
  let parsedItems = [];

  if (Array.isArray(items)) {
    parsedItems = items;
  } else if (typeof items === "string") {
    try {
      parsedItems = JSON.parse(items);
    } catch {
      parsedItems = items ? [items] : [];
    }
  }

  const [checkedState, setCheckedState] = useState({});

  const toggleCheck = (idx) => {
    setCheckedState((prev) => ({
      ...prev,
      [idx]: !prev[idx],
    }));
  };

  const isCheckable = title.toLowerCase().includes("action");

  return (
    <div
      className={`rounded-2xl border p-5 shadow-sm ${
        darkMode
          ? "border-[#2A2E3A] bg-[#12151D]"
          : "border-[#E7E2F5] bg-white"
      }`}
    >
      <div className="mb-3.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span>{icon}</span>
          <h3 className="font-bold text-sm">{title}</h3>
        </div>
        <span className="text-[11px] font-semibold text-slate-400">
          {parsedItems.length}
        </span>
      </div>

      {parsedItems.length === 0 ? (
        <p className="text-xs text-slate-500">None recorded</p>
      ) : (
        <ul className="space-y-2 text-xs leading-5">
          {parsedItems.map((item, index) => {
            const isChecked = !!checkedState[index];
            return (
              <li
                key={index}
                onClick={isCheckable ? () => toggleCheck(index) : undefined}
                className={`flex items-start gap-2 ${
                  isCheckable ? "cursor-pointer select-none" : ""
                }`}
              >
                {isCheckable ? (
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => toggleCheck(index)}
                    className="mt-0.5 rounded text-[#7C4DFF] focus:ring-0 cursor-pointer"
                  />
                ) : (
                  <span className="text-[#7C4DFF] mt-0.5">•</span>
                )}
                <span className={isChecked ? "line-through text-slate-400" : ""}>
                  {item}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default Dashboard;