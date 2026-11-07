using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;

namespace Subsurface.Borehole
{
    [Serializable] public sealed class BoreholeInterval
    {
        public double TopFeet, BottomFeet;
        public string Lithology;
        public string[] SourceValues;
        public int SourceRow;
        public bool IsGap;
        public bool HasImageCoordinates;
        public double TopPixel, BottomPixel;
        public double ThicknessFeet => BottomFeet-TopFeet;
    }
    public sealed class BoreholeDocument
    {
        public readonly List<BoreholeInterval> Intervals = new List<BoreholeInterval>();
        public readonly List<string> Warnings = new List<string>();
        public int SourceIntervalCount;
        public string[] Headers;
        public readonly List<BoreholeInterval> SourceRows=new List<BoreholeInterval>();
        public double TopFeet => Intervals[0].TopFeet;
        public double BottomFeet => Intervals[Intervals.Count-1].BottomFeet;
        public double SpanFeet => BottomFeet-TopFeet;
    }
    public static class BoreholeCsv
    {
        public const int MaximumIntervals=5000, MaximumCharacters=10*1024*1024;
        sealed class Row { public int line; public List<string> fields; }
        public static BoreholeDocument Parse(string csv)
        {
            if(string.IsNullOrWhiteSpace(csv))throw new FormatException("The CSV is empty.");
            if(csv.Length>MaximumCharacters)throw new FormatException("CSV exceeds the 10 MB text limit.");
            var rows=ReadRows(csv.TrimStart('\uFEFF'));if(rows.Count<2)throw new FormatException("A header and at least one interval are required.");
            var header=rows[0].fields;int Column(string name)
            {
                var hits=Enumerable.Range(0,header.Count).Where(i=>string.Equals(header[i].Trim(),name,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(hits.Length!=1)throw new FormatException(hits.Length==0?"Missing required column: "+name:"Duplicate required column: "+name);
                return hits[0];
            }
            int top=Column("top_depth"),bottom=Column("bottom_depth"),lithology=Column("lithology");
            int Optional(string name)=>header.FindIndex(h=>string.Equals(h.Trim(),name,StringComparison.OrdinalIgnoreCase));
            int topPixel=Optional("top_y"),bottomPixel=Optional("bottom_y");
            bool pixels=topPixel>=0&&bottomPixel>=0;
            if(pixels){Column("top_y");Column("bottom_y");}
            var source=new List<BoreholeInterval>();var result=new BoreholeDocument {Headers=header.ToArray()};
            if((topPixel>=0)!=(bottomPixel>=0))result.Warnings.Add("Both top_y and bottom_y are required for document synchronization.");
            foreach(var row in rows.Skip(1))
            {
                if(row.fields.Count!=header.Count)throw new FormatException("CSV row "+row.line+" has "+row.fields.Count+" columns; expected "+header.Count+".");
                double Depth(int index)
                {
                    if(!double.TryParse(row.fields[index].Trim(),NumberStyles.Float,CultureInfo.InvariantCulture,out double d)||!Finite(d)||d<0)
                        throw new FormatException("CSV row "+row.line+": depths must be finite, nonnegative numbers in feet.");
                    return d;
                }
                var interval=new BoreholeInterval {TopFeet=Depth(top),BottomFeet=Depth(bottom),Lithology=row.fields[lithology],SourceRow=row.line,SourceValues=row.fields.ToArray()};
                if(pixels)
                {
                    interval.HasImageCoordinates=double.TryParse(row.fields[topPixel],NumberStyles.Float,CultureInfo.InvariantCulture,out interval.TopPixel)&&double.TryParse(row.fields[bottomPixel],NumberStyles.Float,CultureInfo.InvariantCulture,out interval.BottomPixel)&&Finite(interval.TopPixel)&&Finite(interval.BottomPixel)&&interval.TopPixel>=0&&interval.BottomPixel>interval.TopPixel;
                }
                if(interval.BottomFeet<=interval.TopFeet)throw new FormatException("CSV row "+row.line+": bottom_depth must exceed top_depth.");
                if(string.IsNullOrWhiteSpace(interval.Lithology))result.Warnings.Add("Row "+row.line+": empty lithology retained as an unspecified label.");
                source.Add(interval);if(source.Count>MaximumIntervals)throw new FormatException("This debug viewer supports at most 5,000 source intervals.");
            }
            if(source.Count==0)throw new FormatException("No intervals were found.");
            result.SourceRows.AddRange(source);
            source=source.OrderBy(i=>i.TopFeet).ThenBy(i=>i.SourceRow).ToList();
            for(int i=0;i<source.Count;i++)
            {
                var row=source[i];
                if(i>0)
                {
                    double previous=source[i-1].BottomFeet;
                    if(row.TopFeet<previous)throw new FormatException("Overlapping intervals at source rows "+source[i-1].SourceRow+" and "+row.SourceRow+". The previous visualization was retained.");
                    if(row.TopFeet>previous)result.Intervals.Add(new BoreholeInterval {TopFeet=previous,BottomFeet=row.TopFeet,Lithology="No data",IsGap=true});
                }
                result.Intervals.Add(row);
            }
            result.SourceIntervalCount=source.Count;
            if(!Finite(result.SpanFeet)||result.SpanFeet<=0)throw new FormatException("The depth range cannot be represented safely.");
            return result;
        }
        public static bool Finite(double n)=>!double.IsNaN(n)&&!double.IsInfinity(n);
        static List<Row> ReadRows(string csv)
        {
            var rows=new List<Row>();var fields=new List<string>();var field=new StringBuilder();bool quoted=false,closed=false;int line=1,start=1;
            void FinishField(){fields.Add(field.ToString());field.Clear();closed=false;}
            void FinishRow(){FinishField();if(fields.Exists(s=>!string.IsNullOrWhiteSpace(s)))rows.Add(new Row{line=start,fields=fields});fields=new List<string>();start=line+1;}
            for(int i=0;i<csv.Length;i++)
            {
                char c=csv[i];
                if(quoted)
                {
                    if(c=='"'){if(i+1<csv.Length&&csv[i+1]=='"'){field.Append('"');i++;}else{quoted=false;closed=true;}}
                    else {field.Append(c);if(c=='\n')line++;}
                    continue;
                }
                if(c==','){FinishField();continue;}
                if(c=='\r'||c=='\n') {FinishRow();if(c=='\r'&&i+1<csv.Length&&csv[i+1]=='\n')i++;line++;continue;}
                if(closed){if(c==' '||c=='\t')continue;throw new FormatException("Unexpected text after a quoted field on row "+line+".");}
                if(c=='"'){if(field.Length!=0)throw new FormatException("Unexpected quote on row "+line+".");quoted=true;}
                else field.Append(c);
            }
            if(quoted)throw new FormatException("Unterminated quoted field starting on row "+start+".");
            if(field.Length>0||fields.Count>0||closed)FinishRow();
            return rows;
        }
    }
    public sealed class BoreholeDepthMap
    {
        public readonly double TopFeet, BottomFeet;
        public readonly float DisplayHeight;
        public BoreholeDepthMap(double top,double bottom,float height)
        {if(!BoreholeCsv.Finite(top)||!BoreholeCsv.Finite(bottom)||bottom<=top||!float.IsFinite(height)||height<=0)throw new ArgumentException("Invalid depth mapping");TopFeet=top;BottomFeet=bottom;DisplayHeight=height;}
        public float ToY(double feet)=>(float)(-(feet-TopFeet)/(BottomFeet-TopFeet)*DisplayHeight);
        public double ToFeet(float y)=>TopFeet-y/DisplayHeight*(BottomFeet-TopFeet);
    }
}
