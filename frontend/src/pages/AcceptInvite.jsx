import { useEffect, useState } from "react";
import { useSearchParams, useNavigate, Link } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, tokens, formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { CheckCircle2, ShieldCheck, Lock, ArrowRight, UserCheck, AlertCircle, Eye, EyeOff } from "lucide-react";

function getLoginUrl(email) {
  const targetEmail = email ? encodeURIComponent(email) : "";
  const query = targetEmail ? `?email=${targetEmail}` : "";
  if (typeof window !== "undefined" && window.location.origin) {
    return `${window.location.origin}/login${query}`;
  }
  return `https://app-eta-flax-97.vercel.app/login${query}`;
}

export default function AcceptInvite() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [invite, setInvite] = useState(null);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [accepted, setAccepted] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("No invitation token provided. Please check your invitation email link.");
      setLoading(false);
      return;
    }

    api.get(`/employees/invite/${token}`)
      .then(({ data }) => {
        setInvite(data);
        setLoading(false);
        if (data.already_accepted || data.status === "accepted") {
          tokens.clear();
          toast.info("Invitation already accepted! Redirecting to login page...");
          const targetEmail = data.email || "";
          setTimeout(() => {
            window.location.href = getLoginUrl(targetEmail);
          }, 600);
        }
      })
      .catch((e) => {
        setError(formatApiError(e) || "Invalid or expired invitation link.");
        setLoading(false);
      });
  }, [token]);

  async function handleAccept(e) {
    e.preventDefault();
    if (password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    if (password !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }

    setSubmitting(true);
    try {
      const { data } = await api.post("/employees/accept-invite", { token, password });
      toast.success("Invitation accepted! Redirecting to login page...");
      setAccepted(true);
      tokens.clear();
      const targetEmail = data?.email || invite?.email || "";
      setTimeout(() => {
        window.location.href = getLoginUrl(targetEmail);
      }, 500);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col justify-center items-center bg-slate-950 text-slate-100 p-4 relative overflow-hidden">
      {/* Background Decor */}
      <div className="absolute -top-40 -left-40 w-96 h-96 bg-blue-600/20 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-40 -right-40 w-96 h-96 bg-indigo-600/20 rounded-full blur-3xl pointer-events-none" />

      <div className="w-full max-w-md space-y-6 relative z-10">
        {/* Brand Logo */}
        <div className="text-center space-y-2">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-xs font-semibold uppercase tracking-wider">
            <ShieldCheck className="w-3.5 h-3.5" /> WavyGo OS Workspace
          </div>
          <h1 className="font-display text-3xl font-extrabold tracking-tight text-white">Join Your Teammates</h1>
        </div>

        {loading ? (
          <Card className="border-slate-800 bg-slate-900/80 backdrop-blur-xl text-slate-100 p-8 text-center space-y-3">
            <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto" />
            <p className="text-sm text-slate-400">Verifying invitation token…</p>
          </Card>
        ) : error ? (
          <Card className="border-red-500/30 bg-slate-900/90 backdrop-blur-xl text-slate-100 p-6 space-y-4">
            <div className="flex items-center gap-3 text-red-400">
              <AlertCircle className="w-6 h-6 shrink-0" />
              <h2 className="font-semibold text-base">Invalid Invitation</h2>
            </div>
            <p className="text-sm text-slate-400 leading-relaxed">{error}</p>
            <Button className="w-full bg-slate-800 hover:bg-slate-700 text-white" onClick={() => { tokens.clear(); window.location.href = getLoginUrl(); }}>
              Go to Login
            </Button>
          </Card>
        ) : accepted ? (
          <Card className="border-emerald-500/30 bg-slate-900/90 backdrop-blur-xl text-slate-100 p-6 text-center space-y-5">
            <div className="w-14 h-14 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-full flex items-center justify-center mx-auto">
              <CheckCircle2 className="w-8 h-8" />
            </div>
            <div className="space-y-1">
              <h2 className="font-display text-2xl font-bold text-white">Invitation Accepted!</h2>
              <p className="text-sm text-slate-400">
                Your account is activated and credentials added. Redirecting you to the login page…
              </p>
            </div>
            <Button
              className="w-full bg-blue-600 hover:bg-blue-500 text-white font-medium h-11"
              onClick={() => {
                tokens.clear();
                window.location.href = getLoginUrl(invite?.email);
              }}
            >
              Sign In to Your Workspace <ArrowRight className="w-4 h-4 ml-2" />
            </Button>
          </Card>
        ) : (
          <Card className="border-slate-800 bg-slate-900/90 backdrop-blur-xl text-slate-100 shadow-2xl">
            <CardHeader className="space-y-1">
              <div className="flex items-center justify-between">
                <CardTitle className="text-xl font-display font-bold">Set Up Your Account</CardTitle>
                <Badge variant="secondary" className="bg-blue-500/10 text-blue-400 border border-blue-500/20">
                  {invite?.role || "Employee"}
                </Badge>
              </div>
              <CardDescription className="text-slate-400 text-xs">
                Invited by <span className="text-slate-200 font-medium">{invite?.invited_by || "Administrator"}</span>
              </CardDescription>
            </CardHeader>

            <form onSubmit={handleAccept}>
              <CardContent className="space-y-4">
                {/* Invite Summary */}
                <div className="p-3.5 rounded-lg bg-slate-950/60 border border-slate-800/80 text-xs space-y-1.5">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Name:</span>
                    <span className="font-medium text-slate-200">{invite?.name}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Email:</span>
                    <span className="font-medium text-slate-200">{invite?.email}</span>
                  </div>
                  {invite?.designation && (
                    <div className="flex justify-between">
                      <span className="text-slate-400">Designation:</span>
                      <span className="font-medium text-slate-200">{invite?.designation}</span>
                    </div>
                  )}
                  {invite?.department && (
                    <div className="flex justify-between">
                      <span className="text-slate-400">Department:</span>
                      <span className="font-medium text-slate-200">{invite?.department}</span>
                    </div>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs text-slate-300">Create Password</Label>
                  <div className="relative">
                    <Input
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Minimum 8 characters"
                      required
                      minLength={8}
                      className="bg-slate-950 border-slate-800 text-slate-100 pr-10 focus:border-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200"
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs text-slate-300">Confirm Password</Label>
                  <Input
                    type={showPassword ? "text" : "password"}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter password"
                    required
                    minLength={8}
                    className="bg-slate-950 border-slate-800 text-slate-100 focus:border-blue-500"
                  />
                </div>
              </CardContent>

              <CardFooter className="flex flex-col gap-3 pt-2">
                <Button
                  type="submit"
                  disabled={submitting}
                  className="w-full bg-blue-600 hover:bg-blue-500 text-white font-semibold h-10 transition-colors"
                >
                  {submitting ? (
                    <span className="flex items-center gap-2">
                      <span className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                      Creating Account…
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <UserCheck className="w-4 h-4" /> Accept Invitation & Join
                    </span>
                  )}
                </Button>
                <div className="text-center text-xs text-slate-500">
                  Already have an account?{" "}
                  <Link to="/login" className="text-blue-400 hover:underline">
                    Sign in here
                  </Link>
                </div>
              </CardFooter>
            </form>
          </Card>
        )}
      </div>
    </div>
  );
}
