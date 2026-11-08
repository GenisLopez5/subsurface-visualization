using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using UnityEngine;

namespace Subsurface.Explorer
{
    public static class DocumentResultArchive
    {
        [Serializable]public sealed class Offset {public int offset_y_px=-1,result_width,result_height;}
        public static string Read(string archivePath,string imagePath,out Offset offset)
        {
            using(var archive=ZipFile.OpenRead(archivePath))
            {
                var csv=Find(archive,"_solved.csv");var tif=Find(archive,"_solved.tif");var metadata=Find(archive,"_offset.json");
                if(csv.Length>10L*1024*1024||tif.Length>512L*1024*1024||metadata.Length>1024*1024)throw new IOException("The document result exceeds supported file sizes.");
                string json;using(var reader=new StreamReader(metadata.Open()))json=reader.ReadToEnd();
                offset=new Offset();JsonUtility.FromJsonOverwrite(json,offset);
                if(offset==null||offset.offset_y_px<0)throw new IOException("The document result has no valid offset_y_px.");
                string content;using(var reader=new StreamReader(csv.Open()))content=reader.ReadToEnd();
                // Extract only the requested image to our own generated path, never ZIP entry paths.
                using(var source=tif.Open())using(var destination=File.Create(imagePath))source.CopyTo(destination);
                return content;
            }
        }
        public static string ReadSource(string archivePath,string destination)
        {
            using(var archive=ZipFile.OpenRead(archivePath))
            {
                var entries=archive.Entries.Where(x=>new[]{"_source.tif","_source.tiff","_source.png","_source.jpg","_source.jpeg"}.Any(s=>x.Name.EndsWith(s,StringComparison.OrdinalIgnoreCase))).ToArray();
                if(entries.Length==0)return null;if(entries.Length!=1||entries[0].Length>512L*1024*1024)throw new IOException("Invalid unannotated source image.");
                string target=destination+Path.GetExtension(entries[0].Name);try{using(var input=entries[0].Open())using(var output=File.Create(target))input.CopyTo(output);return target;}catch{if(File.Exists(target))File.Delete(target);throw;}
            }
        }
        static ZipArchiveEntry Find(ZipArchive archive,string suffix)
        {
            var matches=archive.Entries.Where(x=>x.Name.EndsWith(suffix,StringComparison.OrdinalIgnoreCase)).ToArray();
            if(matches.Length!=1)throw new IOException("Expected exactly one "+suffix+" file in the document result.");return matches[0];
        }
        public static byte[] ReadLegend(string archivePath,bool original)
        {
            using(var archive=ZipFile.OpenRead(archivePath))
            {
                string suffix=original?"_legend_original.png":"_legend_generated.png";var entries=archive.Entries.Where(x=>x.Name.EndsWith(suffix,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(entries.Length==0)return null;if(entries.Length!=1||entries[0].Length>16*1024*1024)throw new IOException("Invalid legend in document result.");
                using(var source=entries[0].Open())using(var bytes=new MemoryStream()){source.CopyTo(bytes);return bytes.ToArray();}
            }
        }
    }
}
