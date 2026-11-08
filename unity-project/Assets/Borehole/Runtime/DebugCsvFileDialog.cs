using System;
using System.Runtime.InteropServices;

namespace Subsurface.Borehole
{
    public static class DebugCsvFileDialog
    {
        public static Func<string> EditorPicker;
        public static string SaveCsv(string name)
        {
#if UNITY_EDITOR
            return UnityEditor.EditorUtility.SaveFilePanel("Download CSV","",name,"csv");
#elif UNITY_STANDALONE_WIN
            const int capacity=32768;var buffer=Marshal.AllocHGlobal(capacity*2);
            try{Marshal.Copy((name+'\0').ToCharArray(),0,buffer,name.Length+1);var dialog=new OpenFileName{size=Marshal.SizeOf<OpenFileName>(),owner=GetActiveWindow(),filter="CSV files\0*.csv\0\0",file=buffer,maxFile=capacity,title="Download CSV",defaultExtension="csv",flags=0x00080000|0x00000800|0x00000008|0x2};if(GetSaveFileName(ref dialog))return Marshal.PtrToStringUni(buffer);int error=CommDlgExtendedError();if(error!=0)throw new InvalidOperationException("File dialog error "+error);return null;}finally{Marshal.FreeHGlobal(buffer);}
#else
            throw new PlatformNotSupportedException("CSV file dialogs currently require Windows.");
#endif
        }
        public static Func<string> EditorImagePicker;
        public static string OpenImage()=>Open(true);
        public static string Open()=>Open(false);
        static string Open(bool image)
        {
            var picker=image?EditorImagePicker:EditorPicker;if(picker!=null)return picker();
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            const int capacity=32768;IntPtr buffer=Marshal.AllocHGlobal(capacity*2);
            try
            {
                Marshal.WriteInt16(buffer,0);var dialog=new OpenFileName{size=Marshal.SizeOf<OpenFileName>(),owner=GetActiveWindow(),filter=image?"Document images\0*.tif;*.tiff;*.png;*.jpg;*.jpeg\0\0":"CSV files\0*.csv\0All files\0*.*\0\0",file=buffer,maxFile=capacity,title=image?"Load segmented document":"Load lithology CSV",flags=0x00080000|0x00001000|0x00000800|0x00000008};
                if(GetOpenFileName(ref dialog))return Marshal.PtrToStringUni(buffer);int error=CommDlgExtendedError();if(error!=0)throw new InvalidOperationException("The file picker failed (Windows error "+error+").");return null;
            }
            finally{Marshal.FreeHGlobal(buffer);}
#else
            throw new PlatformNotSupportedException("Use the CSV path field on this platform, or supply CSV text through LoadCsvText.");
#endif
        }
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct OpenFileName
        {
            public int size;public IntPtr owner,instance;[MarshalAs(UnmanagedType.LPWStr)]public string filter;public IntPtr customFilter;public int maxCustomFilter,filterIndex;public IntPtr file;public int maxFile;public IntPtr fileTitle;public int maxFileTitle;[MarshalAs(UnmanagedType.LPWStr)]public string initialDirectory,title;public int flags;public short fileOffset,fileExtension;[MarshalAs(UnmanagedType.LPWStr)]public string defaultExtension;public IntPtr customData,hook;[MarshalAs(UnmanagedType.LPWStr)]public string templateName;public IntPtr reserved;public int reserved2,flagsEx;
        }
        [DllImport("comdlg32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool GetOpenFileName(ref OpenFileName data);
        [DllImport("comdlg32.dll",CharSet=CharSet.Unicode)]static extern bool GetSaveFileName(ref OpenFileName data);
        [DllImport("comdlg32.dll")]static extern int CommDlgExtendedError();
        [DllImport("user32.dll")]static extern IntPtr GetActiveWindow();
#endif
    }
}
